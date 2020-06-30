from . import stats # imports it into everything

from .util import Enum

# a few utility functions
def escape_html(s):
	ret = ""
	for c in s:
		if c in ("<", ">", "&"):
			c = "&#" + str(ord(c)) + ";"
		ret += c
	return ret

def format_datetime(t):
	tzinfo = __import__("datetime").timezone.utc
	return t.replace(tzinfo=tzinfo).strftime("%Y-%m-%d %H:%M UTC")

def format_timedelta(d):
	timedelta = __import__("datetime").timedelta
	l = [
		(timedelta(weeks=1), "w"), (timedelta(days=1), "d"),
		(timedelta(hours=1), "h"), (timedelta(minutes=1), "m"),
	]
	for cmp, char in l:
		if d >= cmp:
			return "%d%c" % (d // cmp, char)
	return "%ds" % d.total_seconds()

# 32-bit FNV-1a
def fnv32a(int_parts, byte_parts) -> int:
	h = 0x811c9dc5
	p = 0x01000193
	for i in int_parts:
		i = abs(i)
		# trivial little endian encoding
		while i != 0:
			h = ((h ^ (i & 0xff)) * p) & 0xffffffff
			i >>= 8
	for bs in byte_parts:
		for b in bs:
			h = ((h ^ b) * p) & 0xffffffff
	return h

## for debugging ##
def dump(obj, name=None, r=False):
	name = (name + ".") if name else ""
	for k in dir(obj):
		if k.startswith("_") or isinstance(getattr(obj.__class__, k, None), property):
			continue
		v = getattr(obj, k)
		if v is None:
			continue
		if r and v.__class__.__name__[0].isupper():
			print("%s%s: %s" % (name, k, v.__class__.__name__))
			dump(v, name + k, r)
		else:
			print("%s%s = %r" % (name, k, v))

# Program version
VERSION = "1.9"

# Ranks
RANKS = Enum({
	"admin": 100,
	"mod": 10,
	"user": 0,
	"banned": -10
})

# Cooldown related
COOLDOWN_TIME_BEGIN = [1, 5, 25, 120, 720, 4320] # begins with 1m, 5m, 25m, 2h, 12h, 3d
COOLDOWN_TIME_LINEAR_M = 4320 # continues 7d, 10d, 13d, 16d, ... (linear)
COOLDOWN_TIME_LINEAR_B = 10080
WARN_EXPIRE_HOURS = 7 * 24

# Karma related
KARMA_PLUS_ONE = 1
KARMA_WARN_PENALTY = 10

# Spam limits
SPAM_LIMIT = 3
SPAM_LIMIT_HIT = 6
SPAM_INTERVAL_SECONDS = 5

# Spam score calculation
SCORE_STICKER = 1.5
SCORE_BASE_MESSAGE = 0.75
SCORE_BASE_FORWARD = 1.25
SCORE_TEXT_CHARACTER = 0.002
SCORE_TEXT_LINEBREAK = 0.1

# other
MESSAGE_EXPIRE_HOURS = 30
MOTD_REMIND_DAYS = 181
