import time

from websockets.sync.client import connect

def main():
    web_socket = 'ws://fluidnc2.local:81'
    connection = connect(web_socket)

    filename = "NearFieldScanner.gcode"
    with open(filename) as diary_file:
        for n, line in enumerate(diary_file, start=1):
            stripped_line = line.rstrip('\n')
            print(f'--> {stripped_line}', flush=True)

            if not line.startswith("?"):
                connection.send(line)
                ready = False
                while not ready:
                    time.sleep(0.01)
                    result = connection.recv()
                    if isinstance(result, bytes):
                        result = result.decode("utf-8")
                    print(f'<-- {result}', flush=True)
                    if "ok" in result:
                        ready = True

            else:
                connection.send(stripped_line)
                time.sleep(0.2)
                result = connection.recv()
                if isinstance(result, bytes):
                    result = result.decode("utf-8")
                print(f'<-- {result}', flush=True)

    connection.close()


if __name__=="__main__":
    main()