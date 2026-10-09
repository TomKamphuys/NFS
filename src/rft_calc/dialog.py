"""Qt layout of the HALS Post reflection-free-time calculator."""
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W,QtSvg
from pathlib import Path
from harmonic_drive_qt.qt_compat import QDoubleSpinBox
from .rft_calculator import calculate_rft
from harmonic_drive_qt.styles import value_spin_style

class Diagram(W.QWidget):
    def __init__(self,dialog):
        super().__init__();self.dialog=dialog;self.setMinimumSize(420,260);self.speaker=QtSvg.QSvgRenderer(str(Path(__file__).resolve().parent/'assets/speaker.svg'))
    def paintEvent(self,event):
        dark=False;bg='#101c28' if dark else '#ffffff';fg='#c2d1df' if dark else '#263e56'
        self.speaker.load(C.QByteArray((Path(__file__).resolve().parent/'assets/speaker.svg').read_bytes().replace(b'#000000',b'#b8d6e8' if dark else b'#263e56')))
        p=G.QPainter(self);p.setRenderHint(G.QPainter.Antialiasing);p.fillRect(self.rect(),G.QColor(bg))
        width,height=self.width(),self.height();floor=height-42;x1,x2=92,width-74
        horizontal,source,mic=[v.value() for v in self.dialog.fields];scale=(height-110)/max(source,mic,1);y1=floor-max(source,1)*scale;y2=floor-max(mic,1)*scale;bounce=x1+(x2-x1)*source/max(source+mic,1)
        def line(x,y,a,b,color,width=2):p.setPen(G.QPen(G.QColor(color),width));p.drawLine(C.QPointF(x,y),C.QPointF(a,b))
        def text(x,y,label):p.setPen(G.QColor(fg));p.drawText(C.QRectF(x-85,y-10,170,22),C.Qt.AlignCenter,label)
        line(16,floor,width-16,floor,fg,3);text(width/2,floor+20,'Reflecting boundary')
        line(x1,y1,x2,y2,'#72d9b3');line(x1,y1,bounce,floor,'#ff9b76');line(bounce,floor,x2,y2,'#ff9b76')
        self.speaker.render(p,C.QRectF(x1-65,y1-32,62,64));p.fillRect(C.QRectF(x2,y2-7,55,14),G.QColor('#94afc7'));p.fillRect(C.QRectF(x2-4,y2-11,8,22),G.QColor('#599bda'))
        for x,y,value in ((x1-16,y1,source),(x2+20,y2,mic)):
            mid=(y+floor)/2;line(x,y,x,mid-12,'#80baff',1);line(x,mid+12,x,floor,'#80baff',1);text(x,mid,f'{value:g} mm')
        text((x1+x2)/2,min(y1,y2)-30,f'Speaker to mic: {horizontal:g} mm')
        text((x1+x2)/2,22,f"RFT: {self.dialog.result['rft_ms']:.2f} ms")
        p.end()

class RFTDialog(W.QDialog):
    def __init__(self,parent,speed=343):
        super().__init__(parent);self.speed=speed;self.setWindowTitle('Reflection Free Time Calculator');self.resize(860,360)
        box=W.QVBoxLayout(self);top=W.QHBoxLayout();box.addLayout(top,1);self.fields=[];self.result={};self.diagram=Diagram(self);top.addWidget(self.diagram,1);side=W.QFormLayout();top.addLayout(side)
        for label,value in [('Speaker to Mic:',50),('Speaker to Boundary:',1000),('Mic to Boundary:',1000)]:
            spin=QDoubleSpinBox();spin.setRange(0,100000);spin.setValue(value);spin.setSuffix(' mm');spin.setObjectName('RFTGeometrySpin');spin.setStyleSheet(value_spin_style('RFTGeometrySpin'));spin.setFixedHeight(32);spin.setFixedWidth(180);self.fields.append(spin);side.addRow(label,spin);spin.valueChanged.connect(self.recalculate)
        self.readout=W.QLabel();side.addRow('RFT',self.readout)
        buttons=W.QDialogButtonBox();buttons.addButton('Close',W.QDialogButtonBox.RejectRole);buttons.addButton('Use This RFT',W.QDialogButtonBox.AcceptRole);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);box.addWidget(buttons);self.recalculate()
    def recalculate(self):
        self.result=calculate_rft(*(f.value() for f in self.fields),self.speed);self.readout.setText(f"{self.result['rft_ms']:.2f} ms")
        self.diagram.update()
