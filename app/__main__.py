#!/usr/bin/env python3
import asyncio
import logging
from logging.config import dictConfig

from tortoise import Tortoise
from .logconfig import LOGCONFIG_DICT

dictConfig(LOGCONFIG_DICT)
logger = logging.getLogger(__name__)

import os
import importlib
import inspect
import config
from config import TG_BOT_NAME, DATABASE_URI
from .telegram import client as tg, tg_start, tg_stop

async def init_db():
	# discover modules
	cur_path = os.path.dirname(os.path.realpath(__file__))
	modules = []
	for f in os.listdir(cur_path):
		if f != "__pycache__" and os.path.isdir(f"{cur_path}/{f}"):
			modules.append(f)

	# initialize modules n shit
	tortoise_models = ['app.models', 'aerich.models']
	for module in modules:
		logger.info(f'loading app.{module}')
		try:
			modname = f"app.{module}.telegram"
			handlers = importlib.import_module(f'.telegram', package=f'app.{module}')
			for funcname, func in inspect.getmembers(handlers, inspect.isfunction):
				if funcname.startswith('handler_'):
					logger.info(f'adding app.{module}.telegram.{funcname}')
					tg.add_event_handler(func)
		except ModuleNotFoundError as e:
			if str(e) != f"No module named '{modname}'":
				logger.exception(f"Error loading {modname}")

		try:
			modname = f"app.{module}.models"
			importlib.import_module('.models', package=f'app.{module}')
			logger.info(f'adding app.{module}.models')
			tortoise_models.append(f'app.{module}.models')
		except ModuleNotFoundError as e:
			if str(e) != f"No module named '{modname}'":
				logger.exception(f"Error loading {modname}")

	try:
		await Tortoise.init(
			db_url=DATABASE_URI,
			modules={"models": tortoise_models},
			use_tz=True
		)
		await Tortoise.generate_schemas()
	except Exception:
		logger.exception("help")

async def startup():
	await init_db()
	await tg_start()

async def shutdown():
	await tg_stop()
	await Tortoise.close_connections()


if __name__ == '__main__':
    logger.info("(Press Ctrl+C to stop this)")
    loop = asyncio.get_event_loop()
    task = loop.create_task(startup())
    try:
        loop.run_forever()
    except KeyboardInterrupt as e:
        logger.info("Quitting...")
        task.cancel()
        loop.run_until_complete(shutdown())
    finally:
        loop.close()
