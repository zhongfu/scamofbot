import logging
from logging.config import dictConfig
from .logconfig import LOGCONFIG_DICT

dictConfig(LOGCONFIG_DICT)
logger = logging.getLogger(__name__)

import os
import importlib
import inspect
import config
from config import TG_BOT_NAME, DATABASE_URI

# discover modules
cur_path = os.path.dirname(os.path.realpath(__file__))
modules = []
for f in os.listdir(cur_path):
	if f != "__pycache__" and os.path.isdir(f"{cur_path}/{f}"):
		modules.append(f)

# initialize modules n shit
tortoise_models = ['bot.models', 'aerich.models']
for module in modules:
	logger.info(f'loading bot.{module}')
	try:
		modname = f"bot.{module}.models"
		importlib.import_module('.models', package=f'bot.{module}')
		logger.info(f'adding bot.{module}.models')
		tortoise_models.append(f'bot.{module}.models')
	except ModuleNotFoundError as e:
		if str(e) != f"No module named '{modname}'":
			logger.exception(f"Error loading {modname}")

TORTOISE_ORM = {
    "connections": {"default": DATABASE_URI},
    "apps": {
        "models": {
            "models": tortoise_models,
            "default_connection": "default",
        },
    },
}
