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
from helper import logToFile, build_menu
from wallet import Wallet
import re
from userdata import UserData


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

        start_handler = CommandHandler('start', self.start)
        self.dispatcher.add_handler(start_handler)
        node_watch_mute_handler = CommandHandler('node_watch_mute', self.node_watch_mute)
        self.dispatcher.add_handler(node_watch_mute_handler)
        node_watch_unmute_handler = CommandHandler('node_watch_unmute', self.node_watch_unmute)
        self.dispatcher.add_handler(node_watch_unmute_handler)
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
        self.userdata = UserData(self.access_whitelist_user)

        # init wallet
        self.LNwallet = Wallet(self.updater.bot, self.userdata, enable_otp=self.otp_enabled)

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

    def add_invoice_menu(self):
        button_list = [
            InlineKeyboardButton("Amount", callback_data="addinvoice_amt"),
            InlineKeyboardButton("Description", callback_data="addinvoice_desc"),
            InlineKeyboardButton("Expiry", callback_data="addinvoice_expiry"),
            InlineKeyboardButton("Generate", callback_data="addinvoice_generate")
        ]
        add_invoice_menu = build_menu(button_list, n_cols=2)
        return InlineKeyboardMarkup(add_invoice_menu)

    def executePayment(self, username, chat_id, code_otp=""):
        invoice_data = self.userdata.get_wallet_payinvoice(username)
        if invoice_data is not None:
            raw_pay_req = invoice_data["raw_invoice"]
            paythread = threading.Thread(
                target=self.LNwallet.payInvoice,
                args=[raw_pay_req, self.updater.bot, chat_id, username, code_otp]
            )
            paythread.start()
        else:
            self.updater.bot.send_message(chat_id=chat_id, text="You don't have any invoice to pay.")

    def addInvoice(self, username, chat_id):
        try:
            self.updater.bot.send_chat_action(chat_id=chat_id, action=telegram.ChatAction.TYPING)
            invoice_data = self.userdata.get_add_invoice_data(username)
            ret, err = self.LNwallet.addInvoice(value=invoice_data["amount"], memo=invoice_data["description"], expiry=invoice_data["expiry"])

            if err is None:
                self.updater.bot.send_chat_action(chat_id=chat_id, action=telegram.ChatAction.TYPING)
                payment_request = ret["pay_req"] if "pay_req" in ret else ret["payment_request"]
                qr = pyqrcode.create(payment_request)
                temp_file_qr = os.path.join(self.root_dir, "temp", str(uuid.uuid4().hex) + ".png")
                qr.png(temp_file_qr, scale=5)

                self.updater.bot.send_photo(chat_id=chat_id, photo=open(temp_file_qr, "rb"))
                self.updater.bot.send_message(chat_id=chat_id, text=payment_request)
                if os.path.exists(temp_file_qr):
                    os.remove(temp_file_qr)
            else:
                self.updater.bot.send_message(chat_id=chat_id, text="I couldn't create invoice, there was an error.")
        except Exception as e:
            logToFile("Exception createInvoice: " + str(e))
            self.updater.bot.send_message(chat_id=chat_id, text="I couldn't create invoice, there was an error.")

    @restricted
    def callback_handle(self, bot, update):
        query = update.callback_query
        param = query.data.split('_')
        username = update.effective_user.username

        bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)

        if param[0] == "payment":
            if param[1] == "yes":
                self.executePayment(username, query.message.chat_id)
            elif param[1] == "no":
                self.cancelPayment(bot, update)
        elif param[0] == "addinvoice":
            if param[1] == "amt":
                self.userdata.set_conv_state(username, "createInvoice_amount")
                bot.send_message(chat_id=query.message.chat_id, text="Write amount in sats or BTC.")
            elif param[1] == "desc":
                self.userdata.set_conv_state(username, "createInvoice_description")
                bot.send_message(chat_id=query.message.chat_id, text="Write description.")
            elif param[1] == "expiry":
                self.userdata.set_conv_state(username, "createInvoice_expiry")
                bot.send_message(chat_id=query.message.chat_id, text="How much time you want invoice to be valid? seconds (add 's' after number) or hours (add 'h')")
            elif param[1] == "generate":
                self.userdata.set_conv_state(username, None)
                self.addInvoice(username, query.message.chat_id)
        else:
            bot.send_message(chat_id=query.message.chat_id, text="callback parameters not valid")

    @restricted
    def start(self, bot, update):
        msg = update["message"]
        if self.userdata.get_chat_id(msg.from_user.username) is None:
            self.userdata.set_chat_id(msg.from_user.username, msg.chat_id)
        bot.send_message(chat_id=msg.chat_id, text="TODO print commands")
        # TODO print commands

    @restricted
    def node_watch_mute(self, bot, update):
        msg = update["message"]
        self.userdata.set_node_watch_mute(msg.from_user.username, True)
        bot.send_message(chat_id=msg.chat_id, text="Node status notifications disabled.")

    @restricted
    def node_watch_unmute(self, bot, update):
        msg = update["message"]
        self.userdata.set_node_watch_mute(msg.from_user.username, False)
        bot.send_message(chat_id=msg.chat_id, text="Node status notifications enabled.")

    @restricted
    def createInvoice(self, bot, update):
        msg = update["message"]
        self.userdata.delete_add_invoice_data(msg.from_user.username)
        bot.send_message(chat_id=msg.chat_id, text="Give me details about invoice or press generate for default values.", reply_markup=self.add_invoice_menu())
        self.userdata.set_conv_state(msg.from_user.username, "createInvoice")

    @restricted
    def msg_handle(self, bot, update):
        msg = update["message"]
        cmd = msg.text
        username = msg.from_user.username

        if self.userdata.get_conv_state(username) == "createInvoice_amount":
            try:
                if cmd.find(".") > 0 or cmd.find(",") > 0:
                    self.userdata.set_add_invoice_data(username, "amount", float(cmd)*100000000)
                else:
                    self.userdata.set_add_invoice_data(username, "amount", int(cmd))
            except Exception as e:
                bot.delete_message(chat_id=msg.chat_id, message_id=msg.message_id)
                bot.send_message(chat_id=msg.chat_id, text="Provided amount value is not valid.", reply_markup=self.add_invoice_menu())
                self.userdata.set_conv_state(msg.from_user.username, "createInvoice")
                return
        if self.userdata.get_conv_state(username) == "createInvoice_description":
            self.userdata.set_add_invoice_data(username, "description", cmd)

        if self.userdata.get_conv_state(username) == "createInvoice_expiry":
            try:
                if cmd[-1:].lower() == "s":
                    expiry = int(cmd[:-1])
                    self.userdata.set_add_invoice_data(username, "expiry", expiry)
                elif cmd[-1:].lower() == "h":
                    expiry = int(cmd[:-1]) * 3600
                    self.userdata.set_add_invoice_data(username, "expiry", expiry)
                else:
                    bot.delete_message(chat_id=msg.chat_id, message_id=msg.message_id)
                    bot.send_message(chat_id=msg.chat_id, text="Provided expiry value is not valid.", reply_markup=self.add_invoice_menu())
                    self.userdata.set_conv_state(msg.from_user.username, "createInvoice")
                    return
            except Exception as e:
                bot.delete_message(chat_id=msg.chat_id, message_id=msg.message_id)
                bot.send_message(chat_id=msg.chat_id, text="Provided expiry value is not valid.", reply_markup=self.add_invoice_menu())
                self.userdata.set_conv_state(msg.from_user.username, "createInvoice")
                return

        if self.userdata.get_conv_state(username) in ["createInvoice_amount", "createInvoice_description", "createInvoice_expiry"]:
            bot.send_message(chat_id=msg.chat_id, text="Give me details about invoice or press generate for default values.", reply_markup=self.add_invoice_menu())
            self.userdata.set_conv_state(msg.from_user.username, "createInvoice")
            return

        if cmd.lower()[:4] in ["lnbc", "lntb"] or cmd.lower()[:10] == "lightning:":  # LN invoice
            value, isValid = self.LNwallet.decodeInvoice(cmd, qr=False)  # isValid = True means it is valid LN invoice
            if isValid:
                msgtext = self.LNwallet.formatDecodedInvoice(value)
                self.userdata.set_wallet_payinvoice(msg.from_user.username, value)
                if self.otp_enabled:
                    bot.send_message(chat_id=msg.chat_id, text=msgtext + "\n<i>send me 2FA code for payment or</i> /cancel_payment", parse_mode=telegram.ParseMode.HTML)
                else:
                    bot.send_message(chat_id=msg.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                    bot.send_message(chat_id=msg.chat_id, text="Do you want to pay this invoice?", reply_markup=self.confirm_menu())
                return

        if re.fullmatch("[0-9]{6}", cmd) is not None:
            self.executePayment(msg.from_user.username, msg.chat_id, code_otp=cmd)
            return

        if "ADD WHITELIST" in cmd.upper():
            params = cmd.split(' ')
            if len(params) == 3:
                username = params[2]
                if username not in self.access_whitelist_user:
                    with open(self.root_dir + "/private/whitelist.txt", "a") as file:
                        file.write(username + "\n")
                    self.access_whitelist_user.append(username)
                    self.userdata.add_new_user(username)
                    bot.send_message(chat_id=msg.chat_id, text=username + " added to whitelist")
                else:
                    bot.send_message(chat_id=msg.chat_id, text=username + " already in whitelist")

        elif "REMOVE WHITELIST" in cmd.upper():
            params = cmd.split(' ')
            if len(params) == 3:
                username = params[2]
                if username in self.access_whitelist_user:
                    self.access_whitelist_user.remove(username)
                    with open(self.root_dir + "/private/whitelist.txt", "w") as file:
                        file.truncate()
                    with open(self.root_dir + "/private/whitelist.txt", "w") as file:
                        for user in self.access_whitelist_user:
                            file.write(user + "\n")
                    self.userdata.remove_user(username)
                    bot.send_message(chat_id=msg.chat_id, text=username + " removed from whitelist")
                else:
                    bot.send_message(chat_id=msg.chat_id, text=username + " not in whitelist")

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
            self.userdata.set_wallet_payinvoice(msg.from_user.username, value)
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

        invoice_data = self.userdata.get_wallet_payinvoice(username)
        if invoice_data is not None:
            amt = invoice_data["decoded"]["num_satoshis"]
            self.userdata.set_wallet_payinvoice(username, None)
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

if __name__ == "__main__":
    nodebot = Bot(otp=False)
    nodebot.run()
