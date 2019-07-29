#!/usr/bin/env python3
import sys
import os
import logging
from datetime import datetime, timedelta
from time import sleep

from blacklist import Database, detect_dbs

# backend

def list_privileged_users(db, cond="rank > 0"):
	sql = "SELECT id, username, realname, rank, left, lastActive FROM users WHERE " + cond
	c = db.execute(sql)
	ret = {}
	for row in c:
		user = ("@" + row[1]) if row[1] is not None else row[2]
		active = None if row[4] is not None else row[5]
		ret[row[0]] = (user, row[3], active)
	return ret

def set_user_rank(db, id, rank):
	db.modify("UPDATE users SET rank = ? WHERE id = ?", (rank, id))

# frontend

def c_list(d, argv):
	"""list <db name> [-a|-m]
		List users with special permissions
		-a to only show admins, -m only mods"""
	if len(argv) not in (1, 2):
		return Exception
	db = d[argv[0]]
	if len(argv) > 1:
		cond = ({"-a": "rank = 100", "-m": "rank = 10"})[argv[1]]
		t = list_privileged_users(db, cond)
	else:
		t = list_privileged_users(db)
	if len(t) == 0:
		return print("No results")
	fmt = "{:<10s} {:<28s} {:>4s} {:^18s}"
	print(fmt.format("ID", "username", "rank", "last active"))
	for id, e in t.items():
		active = str(e[2] or "(left chat)")[:16]
		print(fmt.format(str(id), e[0], str(e[1]), active))

def c_set(d, argv):
	"""set <db name> <user id> [rank]
		Set user permission level (admin, mod, user)
		rank defaults to 0 if not given"""
	if len(argv) not in (2, 3):
		return Exception
	db = d[argv[0]]
	id = int(argv[1])
	rank = "user" if len(argv) < 3 else argv[2]

	if not rank.isdigit():
		rank = ({"admin": 100, "mod": 10, "user": 0})[rank.lower()]
	rank = int(rank)
	if rank not in (100, 10, 0):
		return logging.error("Invalid rank given")
	set_user_rank(db, id, rank)
	logging.info("Success")

def usage(actions):
	print("Utility for managing user permissions (sqlite only)")
	print("Usage: perms.py <action> [arguments...]")
	fmt = "    %-" + str( max(len(f.__doc__.split("\n")[0]) for f in actions.values()) + 4 ) + "s%s"
	print("Actions:")
	for f in actions.values():
		s = list(x.strip() for x in f.__doc__.split("\n"))
		for i, text in enumerate(s[1:]):
			print(fmt % (s[0] if i == 0 else "", text))

def main(argv):
	logging.basicConfig(format="[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M", level=logging.INFO)

	actions = {
		"list": c_list, "set": c_set
	}

	if len(argv) > 0:
		d = detect_dbs()
		if len(d) == 0:
			logging.error("No databases detected, exiting!")
			exit(1)
		logging.info("Detected %d database(s): %s", len(d), ", ".join(d.keys()))

		action = argv[0].lower()
		if action not in actions.keys():
			logging.error("Unknown action")
		else:
			ret = actions[action](d, argv[1:])
			if ret is not Exception: # lol
				exit(0)

	usage(actions)
	exit(1)
		
if __name__ == "__main__":
	main(sys.argv[1:])
