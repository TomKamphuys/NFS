from loguru import logger

logger.add('scanner.log', level="TRACE")


class GrblStreamerMock:
    def cnect(self, port, baudrate):
        logger.debug('cnect')

    def incremental_streaming(self, msg):
        logger.debug('incremental_streaming')

    def poll_start(self):
        logger.debug('poll_start')

    def setup_logging(self):
        logger.debug('setup_logging')
