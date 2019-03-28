import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from functools import wraps
import logging
import os
import json
import pyqrcode, pyotp
import threading
import uuid
from helper import logToFile, parameter_split, build_menu
from wallet import Wallet
import re


def restricted(func):
    @wraps(func)
    def wrapped(self, bot, update, *args, **kwargs):
        if update.callback_query is not None:
            from_user = update.effective_user
            data = update["callback_query"]["message"]
        else:
            data = update["message"]
            from_user = data.from_user

        if (from_user.username is None) or (from_user.username not in self.access_whitelist_user):  # user is not authorized
            with open(self.root_dir + "/unauthorized.txt", "a") as file:
                file.write(data.date.strftime("%Y-%m-%d %H:%M:%S") + "," + str(from_user.username) + "," + str(
                    from_user.first_name) + "," +str(from_user.last_name) + "," + str(from_user.id) + "," + str(from_user.is_bot) + "," + str(data.text) + "\n")
            return
        return func(self, bot, update, *args, **kwargs)
    return wrapped


class Bot:

    root_dir = os.path.dirname(os.path.abspath(__file__))
    access_whitelist_user = []
    userdata = {}

    def __init__(self, otp=False):
        botfile = open(os.path.join(self.root_dir, "private", "telegram_bot_token.json"), "r")
        botcred = json.load(botfile)
        botfile.close()
        self.otp_enabled = otp

        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        self.updater = Updater(token=botcred["token"], request_kwargs={'read_timeout': 6})
        self.dispatcher = self.updater.dispatcher
        msghandler = MessageHandler(Filters.text, self.msg_handle)
        self.dispatcher.add_handler(msghandler)
        imagehandler = MessageHandler(Filters.photo, self.image_handle)
        self.dispatcher.add_handler(imagehandler)
        callbackqhandler = CallbackQueryHandler(self.callback_handle)
        self.dispatcher.add_handler(callbackqhandler)

        comm_handler = CommandHandler('commands', self.commands)
        self.dispatcher.add_handler(comm_handler)
        walletCancelPayHandler = CommandHandler('cancel_payment', self.cancelPayment)
        self.dispatcher.add_handler(walletCancelPayHandler)
        walletOnchainAddressHandler = CommandHandler('wallet_addr', self.walletOnchainAddress)
        self.dispatcher.add_handler(walletOnchainAddressHandler)
        walletBalanceHandler = CommandHandler('wallet_balance', self.walletBalance)
        self.dispatcher.add_handler(walletBalanceHandler)
        walletReceiveHandler = CommandHandler('wallet_receive', self.createInvoice)
        self.dispatcher.add_handler(walletReceiveHandler)
        nodeURIHandler = CommandHandler('node_uri', self.nodeURI)
        self.dispatcher.add_handler(nodeURIHandler)

        if self.otp_enabled is True:
            # generate new 2fA secret if doesn't exist
            if not os.path.isfile(self.root_dir + "/private/OTP.txt"):
                secret = pyotp.random_base32()
                with open(self.root_dir + "/private/OTP.txt", "w") as file:
                    file.write(secret)
                pr_uri = pyotp.totp.TOTP(secret).provisioning_uri("NodeBot")
                qr = pyqrcode.create(pr_uri)
                qr.png(self.root_dir + "/private/OTP.png", scale=5)

        # read whitelist
        with open(self.root_dir + "/private/whitelist.txt", "r") as file:
            self.access_whitelist_user = file.readlines()
        self.access_whitelist_user = [x.strip() for x in self.access_whitelist_user]
        # init user data
        self.init_user_data()

        # init wallet
        self.LNwallet = Wallet(self.userdata, enable_otp=self.otp_enabled)

    def init_user_data(self):
        for user in self.access_whitelist_user:
            self.userdata[user] = {"wallet": {"invoice": None}}

    def run(self):
        self.updater.start_polling()
        logToFile("telegram bot online")

    def stop(self):
        self.updater.stop()
        logToFile("telegram bot message listening stopped")

    def confirm_menu(self):
        button_list = [
            InlineKeyboardButton("Yes", callback_data="payment_yes"),
            InlineKeyboardButton("No", callback_data="payment_no")
        ]
        conf_menu = build_menu(button_list, n_cols=2)
        return InlineKeyboardMarkup(conf_menu)

    def executePayment(self, username, chat_id, code_otp=""):
        invoice_data = self.userdata[username]["wallet"]["invoice"]
        if invoice_data is not None:
            raw_pay_req = invoice_data["raw_invoice"]
            paythread = threading.Thread(
                target=self.LNwallet.payInvoice,
                args=[raw_pay_req, self.updater.bot, chat_id, username, code_otp]
            )
            paythread.start()
        else:
            self.updater.bot.send_message(chat_id=chat_id, text="You don't have any invoice to pay.")

    @restricted
    def callback_handle(self, bot, update):
        query = update.callback_query
        param = query.data.split('_')

        bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)

        if param[0] == "payment":
            if param[1] == "yes":
                self.executePayment(update.effective_user.username, query.message.chat_id)
            elif param[1] == "no":
                self.cancelPayment(bot, update)
        else:
            bot.send_message(chat_id=query.message.chat_id, text="callback parameters not valid")

    @restricted
    def commands(self, bot, update):
        pass

    @restricted
    def msg_handle(self, bot, update):
        msg = update["message"]
        cmd = msg.text

        if cmd.lower()[:4] in ["lnbc", "lntb"] or cmd.lower()[:10] == "lightning:":  # LN invoice
            value, isValid = self.LNwallet.decodeInvoice(cmd, qr=False)  # isValid = True means it is valid LN invoice
            if isValid:
                msgtext = self.LNwallet.formatDecodedInvoice(value)
                self.userdata[msg.from_user.username]["wallet"]["invoice"] = value
                if self.otp_enabled:
                    bot.send_message(chat_id=msg.chat_id, text=msgtext + "\n<i>send me 2FA code for payment or</i> /cancel_payment", parse_mode=telegram.ParseMode.HTML)
                else:
                    bot.send_message(chat_id=msg.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                    bot.send_message(chat_id=msg.chat_id, text="Do you want to pay this invoice?", reply_markup=self.confirm_menu())
                return

        if re.fullmatch("[0-9]{6}", cmd) is not None:
            self.executePayment(msg.from_user.username, msg.chat_id, code_otp=cmd)
            return

    @restricted
    def image_handle(self, bot, update):
        msg = update["message"]

        newFile = bot.get_file(update.message.photo[-1].file_id)
        file_ext = newFile.file_path[newFile.file_path.rfind('.'):]
        temp_filename_local = str(uuid.uuid4().hex) + file_ext
        temp_path_local = os.path.join(self.root_dir, "temp", temp_filename_local)
        newFile.download(temp_path_local)

        value, isValid = self.LNwallet.decodeInvoice(temp_filename_local)  # isValid = True means it is valid LN invoice
        if os.path.exists(temp_path_local):
            os.remove(temp_path_local)
        if isValid:
            msgtext = self.LNwallet.formatDecodedInvoice(value)
            self.userdata[msg.from_user.username]["wallet"]["invoice"] = value
            if self.otp_enabled:
                bot.send_message(chat_id=msg.chat_id, text=msgtext + "\n<i>send me 2FA code for payment or</i> /cancel_payment", parse_mode=telegram.ParseMode.HTML)
            else:
                bot.send_message(chat_id=msg.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                bot.send_message(chat_id=msg.chat_id, text="Do you want to pay this invoice?", reply_markup=self.confirm_menu())
        else:
            bot.send_message(chat_id=msg.chat_id, text="I'm sorry " + value + ".🙀")

    @restricted
    def cancelPayment(self, bot, update):
        if update["message"] is not None:  # command execution
            msg = update["message"]
            username = msg.from_user.username
            chat_id = msg.chat_id
        else:  # call from callback handler
            username = update.effective_user.username
            chat_id = update.callback_query.message.chat_id

        invoice_data = self.userdata[username]["wallet"]["invoice"]
        if invoice_data is not None:
            amt = invoice_data["decoded"]["num_satoshis"]
            self.userdata[username]["wallet"]["invoice"] = None
            bot.send_message(chat_id=chat_id, text="Payment of " + str(amt) + " sats cancelled.")
        else:
            bot.send_message(chat_id=chat_id, text="You don't have any invoice to cancel.")

    @restricted
    def nodeURI(self, bot, update):
        msg = update["message"]

        bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
        try:
            ret, err = self.LNwallet.getInfo()
            if err is None:
                if len(ret["uris"]) > 0:
                    uri = ret["uris"][0]
                else:
                    uri = ret["identity_pubkey"]
                bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
                qr = pyqrcode.create(uri)
                temp_file_qr = os.path.join(self.root_dir, "temp", str(uuid.uuid4().hex) + ".png")
                qr.png(temp_file_qr, scale=5)

                bot.send_photo(chat_id=msg.chat_id, photo=open(temp_file_qr, "rb"))
                bot.send_message(chat_id=msg.chat_id, text=uri)
                if os.path.exists(temp_file_qr):
                    os.remove(temp_file_qr)
            else:
                bot.send_message(chat_id=msg.chat_id, text="I couldn't get node URI, there was an error.")
        except Exception as e:
            logToFile("Exception nodeURI: " + str(e))
            bot.send_message(chat_id=msg.chat_id, text="I couldn't get node URI, there was an error.")

    @restricted
    def walletOnchainAddress(self, bot, update):
        msg = update["message"]

        bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
        try:
            params = msg.text.split(" ")
            if len(params) == 2 and params[1].lower() in ["compatibility", "np2wkh"]:
                type = "np2wkh"
            else:
                type = "p2wkh"

            addr, err = self.LNwallet.getOnchainAddress(type=type)
            if err is None:
                bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
                qr = pyqrcode.create(addr)
                temp_file_qr = os.path.join(self.root_dir, "temp", str(uuid.uuid4().hex) + ".png")
                qr.png(temp_file_qr, scale=5)

                bot.send_photo(chat_id=msg.chat_id, photo=open(temp_file_qr, "rb"))
                bot.send_message(chat_id=msg.chat_id, text=addr)
                if os.path.exists(temp_file_qr):
                    os.remove(temp_file_qr)
            else:
                bot.send_message(chat_id=msg.chat_id, text="I couldn't get address, there was an error.")
        except Exception as e:
            logToFile("Exception walletOnchainAddress: " + str(e))
            bot.send_message(chat_id=msg.chat_id, text="I couldn't get address, there was an error. Check if all parameters are correct.")

    @restricted
    def walletBalance(self, bot, update):
        msg = update["message"]

        bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
        ret, err = self.LNwallet.getBalance()
        if err is None:
            bot.send_message(chat_id=msg.chat_id, text=self.LNwallet.formatBalanceOutput(ret), parse_mode=telegram.ParseMode.HTML)
        else:
            bot.send_message(chat_id=msg.chat_id, text="Error at acquiring balance report.")

    @restricted
    def createInvoice(self, bot, update):  # /receive [amt=amount in sats] [desc="description"] [expiry=number{s|h}]
        msg = update["message"]

        bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
        try:
            success, flags, values = parameter_split(msg.text, valid_flags=["amt", "desc", "expiry"])
            if success is False:
                bot.send_message(chat_id=msg.chat_id, text="Provided parameters are not valid.")
                return

            value_sats = 0
            description = ""
            expiry = 3600
            for idx, flag in enumerate(flags):
                if flag == "amt":
                    value_sats = int(values[idx])
                elif flag == "desc":
                    description = values[idx]
                elif flag == "expiry":
                    if values[idx][-1:].lower() == "s":
                        expiry = int(values[idx][:-1])
                    elif values[idx][-1:].lower() == "h":
                        expiry = int(values[idx][:-1]) * 3600
                    else:
                        bot.send_message(chat_id=msg.chat_id, text="Provided parameters are not valid.")
                        return

            ret, err = self.LNwallet.addInvoice(value=value_sats, memo=description, expiry=expiry)

            if err is None:
                bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
                payment_request = ret["pay_req"] if "pay_req" in ret else ret["payment_request"]
                qr = pyqrcode.create(payment_request)
                temp_file_qr = os.path.join(self.root_dir, "temp", str(uuid.uuid4().hex) + ".png")
                qr.png(temp_file_qr, scale=5)

                bot.send_photo(chat_id=msg.chat_id, photo=open(temp_file_qr, "rb"))
                bot.send_message(chat_id=msg.chat_id, text=payment_request)
                if os.path.exists(temp_file_qr):
                    os.remove(temp_file_qr)
            else:
                bot.send_message(chat_id=msg.chat_id, text="I couldn't create invoice, there was an error.")
        except Exception as e:
            logToFile("Exception createInvoice: " + str(e))
            bot.send_message(chat_id=msg.chat_id, text="I couldn't create invoice, there was an error. Check if all parameters are correct.")


if __name__ == "__main__":
    nodebot = Bot(otp=False)
    nodebot.run()
