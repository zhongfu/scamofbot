from config import TG_BOT_NAME
import os

LOGCONFIG_DICT = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': f'%(asctime)s [%(name)s:%(levelname)s] [%(process)d:%(threadName)s] [%(filename)s:%(lineno)d:%(funcName)s()] %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'file': {
            'class': 'logging.FileHandler',
            'formatter': 'verbose',
            'filename': f'{TG_BOT_NAME}.log',
        },
    },
    'loggers': {
        # `propagate` propagates logs upwards, and is on by default
        # this means that we only need handlers on the root level
        # of course, if we want to log any other logger output to other handlers
        # then we'll need to add a handler for those.
        '': {
            'handlers': ['console', 'file'],
            'level': os.getenv('LOG_LEVEL_ROOT', 'INFO'),
        },
    },
}
