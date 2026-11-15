"""Read-only decay estimates from microphone IRs and raw post-sweep recordings."""
import argparse,csv,json,math,sys
from pathlib import Path


def estimate_ir_decay(signal, fs):
    import numpy as np
    from scipy.signal import resample_poly, butter, sosfilt
    divisor=math.gcd(fs,16000)
    signal=resample_poly(signal,16000//divisor,fs//divisor)
    rate=16000
    peak=int(np.argmax(abs(signal[:1600])))
    result={}
    for label, low, high in [('broadband',40,7000)]+[(str(f),f/np.sqrt(2),f*np.sqrt(2)) for f in (63,125,250,500,1000,2000,4000)]:
        filtered=sosfilt(butter(4,[low,high],btype='bandpass',fs=rate,output='sos'),signal)[peak:]
        power=filtered**2
        noise=float(np.median([np.mean(block) for block in np.array_split(power[-int(.2*rate):],20)]))
        energy=np.cumsum(power[::-1])[::-1]-noise*np.arange(len(power),0,-1)
        energy=np.maximum.accumulate(energy[::-1])[::-1]
        edc=10*np.log10(np.maximum(energy,1e-40)/max(energy[0],1e-40))
        band={}
        for span in (20,30):
            below=np.flatnonzero(edc <= -5-span)
            begin=np.flatnonzero(edc <= -5)
            if not len(below) or not len(begin):continue
            a,b=int(begin[0]),int(below[0])
            if b-a<rate*.005:continue
            time=np.arange(a,b+1)/rate
            y=edc[a:b+1]
            slope,intercept=np.polyfit(time,y,1)
            predicted=slope*time+intercept
            r2=1-float(np.sum((y-predicted)**2)/max(np.sum((y-y.mean())**2),1e-30))
            local_power=float(np.mean(power[max(0,b-80):min(len(power),b+80)]))
            noise_margin=10*np.log10(max(local_power,1e-40)/max(noise,1e-40))
            if slope<0:
                band[f't{span}_rt60_s']=-60/slope
                band[f't{span}_drop_s']=-span/slope
                band[f't{span}_r2']=r2
                band[f't{span}_noise_margin_db']=float(noise_margin)
                band[f't{span}_valid']=bool(r2>=.95 and noise_margin>=10 and b/rate<len(power)/rate-.1)
        result[label]=band
    return result


def analyze(task):
    import numpy as np
    from nfs.review_capture import read_audio,capture_path,load_capture_reference
    from nfs.capture_detection.loopback_reference import locate_marker
    folder,name,metrics,excluded=task
    fs,ir=read_audio(folder/name)
    bands=estimate_ir_decay(ir,fs)
    result=dict(filename=name,review_flagged=excluded,bands=bands)
    fs,raw=read_audio(capture_path(folder,dict(filename=name)))
    metadata,_,marker=load_capture_reference(folder,dict(filename=name))
    layout=metadata['layout'];planned=layout['playback_marker_starts'][-1]
    position=locate_marker(raw[:,0],marker,max(0,planned-round(.1*fs)),len(raw)-len(marker),.6)
    end=position+layout['sweep_offset_samples']+layout['sweep_samples']
    tail=raw[end:,1]
    size=round(.025*fs);n=len(tail)//size
    powers=np.mean(tail[:n*size].reshape(n,size)**2,axis=1)
    floor=float(np.median(powers[-8:]))
    # The rising sweep is not a broadband source switched off at its end.
    # Quantify the available raw decay range instead of fitting an RT to noise.
    result.update(raw_first_100ms_over_floor_db=float(10*np.log10(max(float(np.mean(powers[:4])),1e-40)/max(floor,1e-40))),
                  raw_post_peak_over_floor_db=float(10*np.log10(max(float(np.max(powers)),1e-40)/max(floor,1e-40))),
                  raw_tail_noise_floor_dbfs=float(10*np.log10(max(floor,1e-40))),
                  raw_tail_available_s=len(tail)/fs)
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('dataset',type=Path);parser.add_argument('review_csv',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
    from nfs.review_pool import ReviewProcessPool
    import numpy as np
    folder=args.dataset/'measurement_set'
    rows=list(csv.DictReader(args.review_csv.open(encoding='utf-8')))
    tasks=[(folder,row['filename'],json.loads(row['evidence'])['metrics'],bool(json.loads(row['evidence'])['issues'])) for row in rows]
    pool=ReviewProcessPool()
    results=[]
    try:
        for result in pool.map(analyze,tasks):
            results.append(result)
            if len(results)%200==0:print(f'{len(results)}/{len(rows)}',flush=True)
    finally:pool.close()
    def stats(values):
        values=np.array(values)
        return dict(n=len(values),mean=float(values.mean()),median=float(np.median(values)),p95=float(np.percentile(values,95)),p99=float(np.percentile(values,99))) if len(values) else dict(n=0)
    summary={}
    for label in ('broadband','63','125','250','500','1000','2000','4000'):
        summary[label]={}
        for span in (20,30):
            key=f't{span}'
            selected=[r['bands'][label] for r in results if not r['review_flagged'] and r['bands'][label].get(key+'_valid')]
            summary[label][key+'_rt60']=stats([r[key+'_rt60_s'] for r in selected])
            summary[label][key+'_drop']=stats([r[key+'_drop_s'] for r in selected])
    summary['raw_first_100ms_over_floor_db']=stats([r['raw_first_100ms_over_floor_db'] for r in results if not r['review_flagged']])
    summary['raw_post_peak_over_floor_db']=stats([r['raw_post_peak_over_floor_db'] for r in results if not r['review_flagged']])
    summary['raw_captures_with_30db_range']=sum(r['raw_post_peak_over_floor_db']>=30 for r in results if not r['review_flagged'])
    summary['clean_captures']=sum(not r['review_flagged'] for r in results)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'decay_results.json').write_text(json.dumps(results),encoding='utf-8')
    (args.output/'decay_summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
