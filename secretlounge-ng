#!/usr/bin/env python3
import logging
import yaml
import threading
import sys
import os
import getopt

import src.core as core
import src.telegram as telegram
from src.globals import *
from src.database import JSONDatabase, SQLiteDatabase
from src.cache import Cache
from src.util import Scheduler

def start_new_thread(func, join=False, args=(), kwargs={}):
	t = threading.Thread(target=func, args=args, kwargs=kwargs)
	if not join:
		t.daemon = True
	t.start()
	if join:
		t.join()

def readopt(name):
	global opts
	for e in opts:
		if e[0] == name:
			return e[1]
	return None

def usage():
	print("Usage: %s [-q|-d] [-c file.json]" % sys.argv[0])
	print("Options:")
	print("  -h    Display this text")
	print("  -q    Quiet, set log level to WARNING")
	print("  -d    Debug, set log level to DEBUG")
	print("  -c    Location of config file (default: ./config.yaml)")

def load_config(path):
	with open(configpath, "r") as f:
		config = yaml.safe_load(f)
	# load this variable from another YAML if defined
	tmp = config.get("linked_network")
	if isinstance(tmp, str):
		with open(tmp, "r") as f:
			config["linked_network"] = yaml.safe_load(f)

	return config

def open_db(config):
	type, args = config["database"][0].lower(), config["database"][1:]
	if type == "json":
		return JSONDatabase(*args)
	elif type == "sqlite":
		path = os.path.split(args[0])
		if path[0] != '':
			os.makedirs(path[0], exist_ok=True)
		return SQLiteDatabase(os.path.join(*path))
	else:
		logging.error("Unknown database type.")
		exit(1)

def main(configpath, loglevel=logging.INFO):
	config = load_config(configpath)

	logging.basicConfig(format="%(levelname)-7s [%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=loglevel)
	logging.info("secretlounge-ng v%s starting up", VERSION)

	# Create and initialize various classes
	db = open_db(config)
	ch = Cache()

	core.init(config, db, ch)
	telegram.init(config, db, ch)

	# Set up scheduler
	sched = Scheduler()
	db.register_tasks(sched)
	core.register_tasks(sched)
	telegram.register_tasks(sched)

	# Start all threads
	start_new_thread(telegram.send_thread)
	start_new_thread(sched.run)

	try:
		start_new_thread(telegram.run, join=True)
	except KeyboardInterrupt:
		logging.info("Interrupted, exiting")
		db.close()
		os._exit(1)

if __name__ == "__main__":
	try:
		opts, args = getopt.getopt(sys.argv[1:], "hqdc:", ["help"])
	except getopt.GetoptError as e:
		print(str(e))
		exit(1)
	# Process command line args
	if readopt("-h") is not None or readopt("--help") is not None:
		usage()
		exit(0)
	loglevel = logging.INFO
	if readopt("-q") is not None:
		loglevel = logging.WARNING
	elif readopt("-d") is not None:
		loglevel = logging.DEBUG
	configpath = "./config.yaml"
	if readopt("-c") is not None:
		configpath = readopt("-c")
	# Run the actual program
	main(configpath, loglevel)
