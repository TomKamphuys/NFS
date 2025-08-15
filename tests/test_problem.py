import time

from loguru import logger
from websockets.sync.client import connect

logger.add('problem.log', mode='w', level="TRACE")


def test_run():
    web_socket = 'ws://fluidnc2.local:81'
    connection = connect(web_socket)

    filename = "NearFieldScanner.gcode"
    with open(filename) as diary_file:
        for n, line in enumerate(diary_file, start=1):
            print(n, line.rstrip('\n'), flush=True)
            logger.trace(line)

            if not line.startswith("?"):
                connection.send(line)
                ready = False
                while not ready:
                    time.sleep(0.01)
                    result = connection.recv()
                    if isinstance(result, bytes):
                        result = result.decode("utf-8")
                    logger.trace(f'Received: {result}')
                    print(f'Received: {result}', flush=True)
                    if "ok" in result:
                        ready = True

            else:
                line = line.rstrip('\n')
                connection.send(line)
                print(line, flush=True)
                time.sleep(0.2)
                result = connection.recv()
                if isinstance(result, bytes):
                    result = result.decode("utf-8")
                print(f'Received: {result}', flush=True)


    connection.close()
