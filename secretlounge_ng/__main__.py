import logging
import yaml
import threading
import sys
import os
import getopt

from secretlounge_ng import core, telegram
from secretlounge_ng.globals import *
from secretlounge_ng.database import JSONDatabase, SQLiteDatabase
from secretlounge_ng.cache import Cache
from secretlounge_ng.util import Scheduler
from secretlounge_ng.core import logger

opts = {}

def start_new_thread(func, join=False, args=(), kwargs=None):
	t = threading.Thread(target=func, args=args, kwargs=kwargs)
	if not join:
		t.daemon = True
	t.start()
	if join:
		t.join()

def readopt(name):
	for e in opts:
		if e[0] == name:
			return e[1]
	return None

def usage():
	print("Usage: %s [-q|-d] [-c file]" % sys.argv[0])
	print("Options:")
	print("  -h    Display this text")
	print("  -q    Quiet, set log level to WARNING")
	print("  -d    Debug, set log level to DEBUG")
	print("  -c    Location of config file (default: ./config.yaml)")

def load_config(path):
	with open(path, "r") as f:
		config = yaml.safe_load(f)
	# load this variable from another YAML if defined
	tmp = config.get("linked_network")
	if isinstance(tmp, str):
		with open(tmp, "r") as f:
			config["linked_network"] = yaml.safe_load(f)

	return config

def open_db(config):
	type_, args = config["database"][0].lower(), config["database"][1:]
	if type_ == "json":
		return JSONDatabase(*args)
	elif type_ == "sqlite":
		path = os.path.split(args[0])
		if path[0] != '':
			os.makedirs(path[0], exist_ok=True)
		return SQLiteDatabase(os.path.join(*path))
	else:
		logger.error("Unknown database type.")
		exit(1)

def main():
	global opts
	# Process command line args
	try:
		opts, args = getopt.getopt(sys.argv[1:], "hqdc:", ["help"])
	except getopt.GetoptError as e:
		print(str(e))
		exit(1)

	if len(args) > 0 or readopt("-h") is not None or readopt("--help") is not None:
		usage()
		exit(0)
	loglevel = logger.setLevel(logging.INFO)
	if readopt("-q") is not None:
		loglevel = logger.setLevel(logging.WARNING)
	elif readopt("-d") is not None:
		loglevel = logger.setLevel(logging.DEBUG)
	configpath = readopt("-c") or "./config.yaml"

	# Begin actual startup
	config = load_config(configpath)

	##logging.basicConfig(format="%(levelname)-7s [%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=loglevel)
	logger.info("secretlounge-ng v%s starting up", VERSION)

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
		logger.info("Interrupted, exiting")
		db.close()
		os._exit(1)

if __name__ == "__main__":
	main()
