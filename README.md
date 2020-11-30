secretlounge-ng
---------------
Rewrite of [secretlounge](https://github.com/6697/secretlounge), a bot to make an anonymous group chat on Telegram.

## Setup
```
$ pip3 install -r requirements.txt
$ cp config.yaml.example config.yaml
Edit config.yaml with your favorite text editor.
$ ./secretlounge-ng
```

## @BotFather Setup
Message [@BotFather](https://t.me/BotFather) to configure your bot as follows:

* `/setprivacy`: enabled
* `/setjoingroups`: disabled
* `/setcommands`: paste the command list below

### Command list
```
start - Join the chat (start receiving messages)
stop - Leave the chat (stop receiving messages)
users - Find out how many users are in the chat
info - Get info about your account
sign - Sign a message with your username
s - Alias of sign
tsign - Sign a message with your tripcode
t - Alias of tsign
motd - Show the welcome message
version - Get version & source code of this bot
modhelp - Show commands available to moderators
adminhelp - Show commands available to admins
toggledebug - Toggle debug mode (sends back all messages to you)
togglekarma - Toggle karma notifications
tripcode - Show or set a tripcode for your messages
tripcodetoggle - Toggle tripcode to be on by default on messages
```

## FAQ

1. **How do I unban a blacklisted user from my bot?**

To unban someone you need their Telegram User ID (preferred) or username/profile name.
If you have a name you can use `./util/blacklist.py find` to search your bot's database for the user record.

You can then run `./util/blacklist.py unban 12345678` to remove the ban.

2. **How do I demote somone I promoted to mod/admin at some point?**

If you already have an User ID in mind, proceed below.
Otherwise you can either use the find utility like explained above or run
`./util/perms.py list` to list all users with elevated rank.

Simply run `./util/perms.py set 12345678 user` to remove the users' privileges.

This can also be used to grant an user higher privileges by exchanging the last argument with "*mod*" or "*admin*".

3. **What is the suggested setup to run multiple bots?**

The `blacklist.py` and `perms.py` script, including advanced functions like blacklist syncing
(`./util/blacklist.py sync`), support a structure like the following where each bot
has its' own subdirectory:

```
root folder
\-- bot1
  \-- db.sqlite
  \-- config.yaml
\-- bot2
  \-- db.sqlite
  \-- ...
\-- ...
\-- README.md
\-- secretlounge-ng
```
