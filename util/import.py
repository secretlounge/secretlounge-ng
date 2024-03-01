#!/usr/bin/env python3
import os
import logging
import yaml
import sys
import json
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), ".."))
from secretlounge_ng_remix.globals import *
from secretlounge_ng_remix.database import User, SystemConfig, JSONDatabase, SQLiteDatabase
from secretlounge_ng_remix.__main__ import open_db, load_config

def safe_time(n):
	if n > 2**32:
		n = 2**32
	return datetime.utcfromtimestamp(n)

def usage():
	print("Import database from legacy secretlounge instances")
	print("Usage: import.py <config file> <original db>")

def main(configpath, importpath):
	config = load_config(configpath)

	logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M", level=logging.INFO)

	db = open_db(config)

	with open(importpath, "r") as f:
		data = json.load(f)

	had_ids = set()
	for j in data["users"]:
		u = User()
		u.id = j["id"]
		u.username = j.get("username", None)
		u.realname = j.get("realname", "")
		u.rank = j["rank"]
		u.joined = safe_time(0)
		if j.get("left", False) != False:
			u.left = safe_time(j["left"] // 1000)
		u.lastActive = u.joined
		if "banned" in j.keys():
			u.cooldownUntil = safe_time(j["banned"] // 1000)
		if "reason" in j.keys():
			u.blacklistReason = j["reason"]
		u.warnings = j.get("warnings", 0)
		if u.warnings > 0:
			u.warnExpiry = safe_time(j["warnUpdated"] // 1000) + timedelta(hours=WARN_EXPIRE_HOURS)
		u.karma = j.get("karma", 0)
		u.hideKarma = j.get("hideKarma", False)
		u.debugEnabled = j.get("debug", False)

		if u.id in had_ids:
			logging.warning("%s is duplicate, dropping the second one", u)
		else:
			db.addUser(u)
			had_ids.add(u.id)

	c = SystemConfig()
	c.motd = data["system"]["motd"]
	db.setSystemConfig(c)

	logging.info("Success.")
	db.close()

if __name__ == "__main__":
	if len(sys.argv) < 3:
		usage()
		exit(1)
	main(sys.argv[1], sys.argv[2])
