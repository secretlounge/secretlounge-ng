#!/usr/bin/env python3
import sys
import os
import logging
import sqlite3

from datetime import datetime
from time import sleep

def open_db(path):
	db = sqlite3.connect(path,
		detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
	db.row_factory = sqlite3.Row
	return db

def detect_dbs():
	d = {}
	# expects the following directory structure:
	#   root dir
	#   \ bot1
	#     \ db.sqlite
	#     \ ...
	#   \ bot2
	#     \ db.sqlite
	#     \ ...
	#   \ README.md
	#   \ secretlounge-ng
	#   \ ...
	for e in os.scandir("."):
		if e.is_dir():
			path = os.path.join(e.path, "db.sqlite")
			if os.path.exists(path):
				d[e.name] = open_db(path)
	return d

def modify_db(db, f):
	while True:
		try:
			f()
		except sqlite3.OperationalError as e:
			if "database is locked" in str(e):
				continue # just retry, sqlite will do the waiting for us
			raise
		break
	db.commit()

def ban_user(db, id, reason):
	c = db.execute("SELECT COUNT(*) FROM users WHERE id = ?", (id, ))
	if c.fetchone()[0] == 0:
		# user was never here, add an placeholder entry to still ban them
		nodate = datetime.utcfromtimestamp(0)
		u = {
			"id": id,
			"realname": "",
			"rank": -10,
			"joined": nodate,
			"left": nodate,
			"lastActive": nodate,
			"blacklistReason": reason,
			"warnings": 0,
			"karma": 0,
			"hideKarma": 0,
			"debugEnabled": 0,
		}
		sql = "INSERT INTO users (" + ( ", ".join(u.keys()) ) + ") VALUES (" + ( ", ".join("?" for _ in u) ) + ")"
		f = lambda: db.execute(sql, tuple(u.values()))
		modify_db(db, f)
		return 0, 1
	# is this user already banned?
	c = db.execute("SELECT COUNT(*) FROM users WHERE id = ? AND rank > ?", (id, -10))
	if c.fetchone()[0] == 0:
		return 0, 0
	# update user values to ban them
	param = (-10, datetime.now(), reason, id)
	f = lambda: db.execute("UPDATE users SET rank = ?, left = ?, blacklistReason = ? WHERE id = ?", param)
	modify_db(db, f)
	return 1, 0

def unban_user(db, id):
	c = db.execute("SELECT realname, left FROM users WHERE id = ? AND rank = ?", (id, -10))
	row = c.fetchone()
	if row is None:
		return 0
	if row[0] == "" and row[1] == datetime.utcfromtimestamp(0):
		# this is a placeholder entry, just delete it instead
		f = lambda: db.execute("DELETE FROM users WHERE id = ?", (id, ))
	else:
		f = lambda: db.execute("UPDATE users SET rank = ?, blacklistReason = NULL WHERE id = ?", (0, id))
	modify_db(db, f)
	return 1

def sync(d):
	interval = 60
	last_update = datetime.utcfromtimestamp(0.0)
	logging.info("Running periodic blacklist sync (every %ds)", interval)
	while True:
		now = datetime.now()
		# find all blacklists that happened since our last update
		l = []
		for name, db in d.items():
			c = db.execute("SELECT id, blacklistReason FROM users WHERE rank = ? AND left >= ?", (-10, last_update))
			for row in c:
				l.append((row[0], row[1] or "", name))
		# apply the same bans on other instances
		for id, reason, from_name in l:
			reason = reason + " [" + from_name + "]"
			stat1, stat2 = 0, 0
			for db in d.values():
				a, b = ban_user(db, id, reason)
				stat1 += a; stat2 += b
			if stat1 + stat2 > 0:
				logging.info("Transferred ban of user id %d orignated from %s (%d-%d)", id, from_name, stat1, stat2)
		# Zzz..
		last_update = now
		sleep(interval)

def usage():
	print("Utility for managing blacklists (sqlite only)")
	print("Usage: blacklist.py <action> [arguments...]")
	print("Actions:")
	print("  ban <user id> [reason]    Manually blacklist specified user")
	print("  unban <user id>           Unban specified user")
	print("  sync                      Synchronize blacklisted users (runs as daemon)")

def main(argv):
	logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M", level=logging.INFO)

	d = detect_dbs()
	if len(d) == 0:
		logging.error("No databases detected, exiting!")
		exit(1)
	else:
		logging.info("Detected %d databases: %s", len(d), ", ".join(d.keys()))

	action = "" if len(argv) < 1 else argv[0].lower()
	if action == "ban":
		if len(argv) >= 2:
			id = int(argv[1])
			reason = " ".join(argv[2:])
			stat1, stat2 = 0, 0
			for db in d.values():
				a, b = ban_user(db, id, reason)
				stat1 += a; stat2 += b
			return logging.info("Success. (%d-%d)", stat1, stat2)
	elif action == "unban":
		if len(argv) >= 2:
			id = int(argv[1])
			stat = 0
			for db in d.values():
				stat += unban_user(db, id)
			if stat == 0:
				return logging.warning("This user wasn't blacklisted anywhere.")
			return logging.info("Success. (%d)", stat)
	elif action == "sync":
		return sync(d)

	# something went wrong
	usage()
	exit(1)
		
if __name__ == "__main__":
	main(sys.argv[1:])
