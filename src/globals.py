from src.util import Enum

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

## for debugging ##
def dump(obj, name=None, r=False):
	name = "" if name is None else (name + ".")
	for e, ev in ((e, getattr(obj, e)) for e in dir(obj)):
		if e.startswith("_") or ev is None:
			continue
		if r and ev.__class__.__name__[0].isupper():
			print("%s%s (%s)" % (name, e, ev.__class__.__name__))
			dump(ev, name + e, r)
		else:
			print("%s%s = %r" % (name, e, ev))

# Program version
VERSION = "1.0"

# Ranks
RANKS = Enum({
	"admin": 100,
	"mod": 10,
	"user": 0,
	"banned": -10
})

# Cooldown related
BASE_COOLDOWN_MINUTES = 5
WARN_EXPIRE_HOURS = 7 * 24

# Karma related
KARMA_PLUS_ONE = 1
KARMA_WARN_PENALTY = 10

# Spam limits
SPAM_LIMIT = 3
SPAM_LIMIT_HIT = 5
SPAM_INTERVAL_SECONDS = 5

# Spam score calculation
SCORE_MESSAGE = 0.75
SCORE_LINK = 0.25
SCORE_CHARACTER = 0.004
SCORE_STICKER = 1.5
