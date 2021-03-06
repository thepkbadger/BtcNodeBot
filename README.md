# BtcNodeBot
Telegram bot for managing Lightning Bitcoin Full Node. It can be used as a non-custodial wallet.
You can send and receive On-Chain transactions, open and close lightning channels, create and pay LN invoices, check balance.

Bot can be installed on the machine with lnd instance running, or other machines in the network
that have access to lnd gRPC API.

## installation
Python >= 3.5 needs to be installed. On Linux and Mac OS you will need to install some additional packages.
#### Linux
```
sudo apt-get install libzbar0
```
#### Mac OS X
```
brew install zbar
```

Dependencies can be installed by:
```
$ pip install -r requirements.txt
```

Start bot with:
```
$ python3 run.py
```

## configuration
Before using you will need to edit config file *private/btcnodebot.conf*.
##### required
1. Talk to [@BotFather](https://telegram.me/botfather) to create telegram bot. [(How do I create a bot?)](https://core.telegram.org/bots#3-how-do-i-create-a-bot)
Copy access token you get to configuration file.
2. In configuration file provide telegram username or list of usernames that will have access to bot.

- Bot requires lnd instance installed and access to cert and admin macaroon file. 
If lnd is installed with default values everything should work, else set appropriate values in *btcnodebot.conf*.

##### optional
- Optionally you can enable Two-factor auth by setting bototp=1,
it is used when opening, closing channels, sending on-chain tx or paying invoices. 
When enabled *private/OTP.png* and *private/OTP.txt* are generated at start.
You can scan QR code (OTP.png) or enter OTP secret (OTP.txt) in your 2FA app (Google Authenticator,...).

- It is recommended that you protect *./private* directory with appropriate permissions, Linux:
```
chmod -R 700 ./private
```

## usage
For convenience, when typing commands to your bot you can get suggestions.
Talk to [@BotFather](https://telegram.me/botfather) and type */setcommands*, then copy contents of *list_of_commands.txt* to the chat.

#### usage examples
- pay LN invoice

![](examples/pay_invoice.gif)

- open LN channel

![](examples/open_ch.gif)

- close LN channel

![](examples/close_ch.gif)

- On-Chain transaction

![](examples/onchain_send.gif)

- Settings

![](examples/settings.gif)