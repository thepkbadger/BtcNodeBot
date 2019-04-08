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
from helper import logToFile, build_menu, amount_parse, formatAmount
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

        with open(self.root_dir + "/list_of_commands.txt", "r") as file:
            self.commands = file.readlines()

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
        btc_unit_handler = CommandHandler('bitcoin_unit', self.select_unit)
        self.dispatcher.add_handler(btc_unit_handler)
        node_watch_mute_handler = CommandHandler('node_watch_mute', self.node_watch_mute)
        self.dispatcher.add_handler(node_watch_mute_handler)
        node_watch_unmute_handler = CommandHandler('node_watch_unmute', self.node_watch_unmute)
        self.dispatcher.add_handler(node_watch_unmute_handler)
        walletCancelPayHandler = CommandHandler('cancel_payment', self.cancelPayment)
        self.dispatcher.add_handler(walletCancelPayHandler)
        walletOnchainAddressHandler = CommandHandler('onchain_addr', self.walletOnchainAddress)
        self.dispatcher.add_handler(walletOnchainAddressHandler)
        walletBalanceHandler = CommandHandler('wallet_balance', self.walletBalance)
        self.dispatcher.add_handler(walletBalanceHandler)
        walletReceiveHandler = CommandHandler('receive', self.createInvoice)
        self.dispatcher.add_handler(walletReceiveHandler)
        nodeURIHandler = CommandHandler('node_uri', self.nodeURI)
        self.dispatcher.add_handler(nodeURIHandler)
        walletListChannelsHandler = CommandHandler('list_channels', self.listChannels)
        self.dispatcher.add_handler(walletListChannelsHandler)
        walletOpenChannelHandler = CommandHandler('open_channel', self.openChannel)
        self.dispatcher.add_handler(walletOpenChannelHandler)
        walletCancelOpenChHandler = CommandHandler('cancel_opening_channel', self.cancelOpeningChannel)
        self.dispatcher.add_handler(walletCancelOpenChHandler)

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

    # ------------------------------- Keyboard Menus
    def unit_menu(self):
        button_list = [
            InlineKeyboardButton("Bitcoin (BTC)", callback_data="unit_BTC"),
            InlineKeyboardButton("Millibitcoin (mBTC)", callback_data="unit_mBTC"),
            InlineKeyboardButton("Microbitcoin (bits)", callback_data="unit_bits"),
            InlineKeyboardButton("Satoshis (sats)", callback_data="unit_sats")
        ]
        unit_menu = build_menu(button_list, n_cols=1)
        return InlineKeyboardMarkup(unit_menu)

    def confirm_menu(self, type="payment"):
        button_list = [
            InlineKeyboardButton("Yes", callback_data=type + "_yes"),
            InlineKeyboardButton("No", callback_data=type + "_no")
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

    def new_address_menu(self):
        button_list = [
            InlineKeyboardButton("Compatibility (np2wkh)", callback_data="newaddress_compatibility"),
            InlineKeyboardButton("Native SegWit (bech32)", callback_data="newaddress_nativesegwit")
        ]
        new_address_menu = build_menu(button_list, n_cols=2)
        return InlineKeyboardMarkup(new_address_menu)

    def open_channel_menu(self):
        button_list = [
            InlineKeyboardButton("Node URI", callback_data="opench_addr"),
            InlineKeyboardButton("Amount", callback_data="opench_lamount"),
            InlineKeyboardButton("Target Conf", callback_data="opench_tconf"),
            InlineKeyboardButton("Fee", callback_data="opench_fee"),
            InlineKeyboardButton("Private", callback_data="opench_private"),
            InlineKeyboardButton("Min HTLC", callback_data="opench_minhtlc"),
            InlineKeyboardButton("Time Lock", callback_data="opench_csv"),
            InlineKeyboardButton("Open channel ->", callback_data="opench_execute")
        ]
        open_channel_menu = build_menu(button_list, n_cols=2)
        return InlineKeyboardMarkup(open_channel_menu)

    def channel_list_menu(self, username, page=0, per_page=5):
        channels, err = self.LNwallet.getChannels(page, per_page)
        if err is not None:
            return None, err
        button_list = []

        for ch in channels["channels"]:
            button_header = ch["alias"] + " Cap: " + formatAmount(int(ch["capacity"]), self.userdata.get_selected_unit(username))
            button = InlineKeyboardButton(button_header, callback_data="ch_"+ch["chan_id"])
            button_list.append(button)

        if channels["last"] is False and page > 0:
            button_list.append(InlineKeyboardButton("<<--", callback_data="ch_back"))
            button_list.append(InlineKeyboardButton("-->>", callback_data="ch_forward"))
        elif channels["last"] is True and page > 0:
            button_list.append(InlineKeyboardButton("<<--", callback_data="ch_back"))
        elif channels["last"] is False and page == 0:
            button_list.append(InlineKeyboardButton("-->>", callback_data="ch_forward"))

        channels_menu = build_menu(button_list, n_cols=1)
        return InlineKeyboardMarkup(channels_menu), None

    # -------------------------------
    def executeOpeningChannel(self, username, chat_id, code_otp=""):
        data = self.userdata.get_open_channel_data(username)
        openchthread = threading.Thread(
            target=self.LNwallet.openChannel,
            args=[self.updater.bot, chat_id, username, data["address"], data["local_amount"], data["target_conf"],
                  data["sat_per_byte"], data["private"], data["min_htlc_msat"], data["remote_csv_delay"], code_otp
                  ]
        )
        openchthread.start()
        self.userdata.delete_open_channel_data(username)
        self.userdata.set_conv_state(username, None)

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

        self.userdata.set_wallet_payinvoice(username, None)
        self.userdata.set_conv_state(username, None)

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
                self.updater.bot.send_message(chat_id=chat_id, text="I couldn't create invoice, "+str(err)+".")
        except Exception as e:
            logToFile("Exception addInvoice: " + str(e))
            self.updater.bot.send_message(chat_id=chat_id, text="I couldn't create invoice, there was an error.")

    def getNewOnchainAddress(self, chat_id, address_type):
        try:
            self.updater.bot.send_chat_action(chat_id=chat_id, action=telegram.ChatAction.TYPING)
            addr, err = self.LNwallet.getOnchainAddress(type=address_type)
            if err is None:
                qr = pyqrcode.create(addr)
                temp_file_qr = os.path.join(self.root_dir, "temp", str(uuid.uuid4().hex) + ".png")
                qr.png(temp_file_qr, scale=5)

                self.updater.bot.send_photo(chat_id=chat_id, photo=open(temp_file_qr, "rb"))
                self.updater.bot.send_message(chat_id=chat_id, text=addr)
                if os.path.exists(temp_file_qr):
                    os.remove(temp_file_qr)
            else:
                self.updater.bot.send_message(chat_id=chat_id, text="I couldn't get address, there was an error.")
        except Exception as e:
            logToFile("Exception getNewOnchainAddress: " + str(e))
            self.updater.bot.send_message(chat_id=chat_id, text="I couldn't get address, there was an error. Check if all parameters are correct.")

    def getChannelsPage(self, username, chat_id, page):
        self.updater.bot.send_chat_action(chat_id=chat_id, action=telegram.ChatAction.TYPING)
        markup, err = self.channel_list_menu(username, page)
        if err is None:
            self.userdata.set_pagination(username, page)
            self.updater.bot.send_message(chat_id=chat_id, text="<b>Opened LN channels "+str(page+1)+"</b>", reply_markup=markup, parse_mode=telegram.ParseMode.HTML)
        else:
            self.updater.bot.send_message(chat_id=chat_id, text="Cannot list opened channels, " + err)

    # ------------------------------- Callbacks
    @restricted
    def callback_handle(self, bot, update):
        query = update.callback_query
        param = query.data.split('_')
        username = update.effective_user.username

        bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)

        if param[0] == "unit":
            self.userdata.set_selected_unit(username, param[1])
            bot.send_message(chat_id=query.message.chat_id, text=param[1]+" selected.")
        elif param[0] == "payment":
            if param[1] == "yes":
                self.executePayment(username, query.message.chat_id)
            elif param[1] == "no":
                self.cancelPayment(bot, update)

        elif param[0] == "addinvoice":
            if param[1] == "amt":
                self.userdata.set_conv_state(username, "createInvoice_amount")
                bot.send_message(chat_id=query.message.chat_id, text="Write amount in sats or BTC. (e.g. 10000 or 0.0001)")
            elif param[1] == "desc":
                self.userdata.set_conv_state(username, "createInvoice_description")
                bot.send_message(chat_id=query.message.chat_id, text="Write description.")
            elif param[1] == "expiry":
                self.userdata.set_conv_state(username, "createInvoice_expiry")
                bot.send_message(chat_id=query.message.chat_id, text="How much time you want invoice to be valid? seconds (add 's' after number) or hours (add 'h')")
            elif param[1] == "generate":
                self.userdata.set_conv_state(username, None)
                self.addInvoice(username, query.message.chat_id)

        elif param[0] == "newaddress":
            if param[1] == "compatibility":
                self.getNewOnchainAddress(query.message.chat_id, "np2wkh")
            elif param[1] == "nativesegwit":
                self.getNewOnchainAddress(query.message.chat_id, "p2wkh")

        elif param[0] == "ch":
            if param[1] == "forward":
                page = self.userdata.get_pagination(username)
                self.getChannelsPage(username, query.message.chat_id, page+1)
            elif param[1] == "back":
                page = self.userdata.get_pagination(username)
                if page > 0:
                    self.getChannelsPage(username, query.message.chat_id, page - 1)
            else:
                bot.send_chat_action(chat_id=query.message.chat_id, action=telegram.ChatAction.TYPING)
                ch_data, err = self.LNwallet.getChannelData(chan_id=param[1])
                if err is not None:
                    bot.send_message(chat_id=query.message.chat_id, text="Cannot get channel data, " + err)
                else:
                    formated = self.LNwallet.formatChannelOutput(ch_data, username)
                    bot.send_message(chat_id=query.message.chat_id, text=formated, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)
                page = self.userdata.get_pagination(username)
                self.getChannelsPage(username, query.message.chat_id, page)

        elif param[0] == "opench":
            if param[1] == "addr":
                self.userdata.set_conv_state(username, "openChannel_addr")
                bot.send_message(chat_id=query.message.chat_id, text="Enter node's publickey@host")
            elif param[1] == "lamount":
                self.userdata.set_conv_state(username, "openChannel_lamount")
                bot.send_message(chat_id=query.message.chat_id, text="Write amount in sats or BTC. (e.g. 10000 or 0.0001)")
            elif param[1] == "tconf":
                self.userdata.set_conv_state(username, "openChannel_tconf")
                bot.send_message(chat_id=query.message.chat_id, text="Enter the target number of blocks that the funding transaction should be confirmed by.")
            elif param[1] == "fee":
                self.userdata.set_conv_state(username, "openChannel_fee")
                bot.send_message(chat_id=query.message.chat_id, text="Enter fee in sat/byte, for funding transaction.")
            elif param[1] == "private":
                self.userdata.set_conv_state(username, "openChannel_private")
                bot.send_message(chat_id=query.message.chat_id, text="Write 'yes' for channel to be private (not announced to the greater network), else 'no'.")
            elif param[1] == "minhtlc":
                self.userdata.set_conv_state(username, "openChannel_minhtlc")
                bot.send_message(chat_id=query.message.chat_id, text="Write amount in millisatoshi.\nThis is the minimum value we will require for incoming payments on the channel. Default is 1000 msat = 1 sat.")
            elif param[1] == "csv":
                self.userdata.set_conv_state(username, "openChannel_csv")
                bot.send_message(chat_id=query.message.chat_id, text="Enter number of blocks.\nIf this channel is closed uncooperatively this is the number of blocks remote peer will have to wait before claiming funds.")
            elif param[1] == "execute":
                self.userdata.set_conv_state(username, "openChannel_execute")
                data = self.userdata.get_open_channel_data(username)
                if data["address"] == "" or data["local_amount"] <= 0:
                    self.updater.bot.send_message(chat_id=query.message.chat_id, text="Opening channel failed, Node URI and Amount are required.")
                    self.userdata.delete_open_channel_data(username)
                    self.userdata.set_conv_state(username, None)
                else:
                    msgtext = self.LNwallet.formatChannelOpenOutput(self.userdata.get_open_channel_data(username), username)
                    if self.otp_enabled:
                        self.userdata.set_conv_state(username, "openChannel_otp")
                        bot.send_message(chat_id=query.message.chat_id, text=msgtext + "\n\n<i>send me 2FA code to open or</i> /cancel_opening_channel", parse_mode=telegram.ParseMode.HTML)
                    else:
                        bot.send_message(chat_id=query.message.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                        bot.send_message(chat_id=query.message.chat_id, text="Do you want to open this channel?", reply_markup=self.confirm_menu(type="opench"))
            elif param[1] == "yes":
                self.executeOpeningChannel(username, query.message.chat_id)
            elif param[1] == "no":
                self.cancelOpeningChannel(bot, update)

        else:
            bot.send_message(chat_id=query.message.chat_id, text="callback parameters not valid")

    # ------------------------------- msg and image handlers
    @restricted
    def msg_handle(self, bot, update):
        msg = update["message"]
        cmd = msg.text
        username = msg.from_user.username
        conv_state = self.userdata.get_conv_state(username)

        # --------- creating invoice
        if conv_state in ["createInvoice_amount", "createInvoice_description", "createInvoice_expiry"]:
            try:
                values_valid = True
                if conv_state == "createInvoice_amount":
                    value, values_valid = amount_parse(cmd)
                    self.userdata.set_add_invoice_data(username, "amount", value)
                elif conv_state == "createInvoice_description":
                    self.userdata.set_add_invoice_data(username, "description", cmd)
                elif conv_state == "createInvoice_expiry":
                    if cmd[-1:].lower() == "s":
                        expiry = int(cmd[:-1])
                        self.userdata.set_add_invoice_data(username, "expiry", expiry)
                    elif cmd[-1:].lower() == "h":
                        expiry = int(cmd[:-1]) * 3600
                        self.userdata.set_add_invoice_data(username, "expiry", expiry)
                    else:
                        values_valid = False

                if values_valid:
                    respText = "Give me details about invoice or press generate."
                else:
                    respText = "<b>Provided value is not valid.</b>"

                bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.add_invoice_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "createInvoice")
                return

            except Exception as e:
                bot.send_message(chat_id=msg.chat_id, text="<b>Provided value is not valid.</b>", reply_markup=self.add_invoice_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "createInvoice")
                return

        # --------- opening channel
        if conv_state in ["openChannel_addr", "openChannel_lamount", "openChannel_tconf", "openChannel_fee",
                          "openChannel_private", "openChannel_minhtlc", "openChannel_csv"]:
            try:
                values_valid = True
                if conv_state == "openChannel_addr":
                    self.userdata.set_open_channel_data(username, "address", str(cmd))
                elif conv_state == "openChannel_lamount":
                    value, values_valid = amount_parse(cmd)
                    self.userdata.set_open_channel_data(username, "local_amount", value)
                elif conv_state == "openChannel_tconf":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_open_channel_data(username, "target_conf", value)
                    else:
                        values_valid = False
                elif conv_state == "openChannel_fee":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_open_channel_data(username, "sat_per_byte", value)
                    else:
                        values_valid = False
                elif conv_state == "openChannel_private":
                    private = False
                    if cmd.lower() == "yes":
                        private = True
                    elif cmd.lower() == "no":
                        private = False
                    else:
                        values_valid = False
                    self.userdata.set_open_channel_data(username, "private", private)
                elif conv_state == "openChannel_minhtlc":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_open_channel_data(username, "min_htlc_msat", value)
                    else:
                        values_valid = False
                elif conv_state == "openChannel_csv":
                    self.userdata.set_open_channel_data(username, "remote_csv_delay", int(cmd))

                if values_valid:
                    respText = "Enter <i>Node URI</i> and <i>Amount</i>, everything else is optional."
                else:
                    respText = "<b>Provided value is not valid.</b>"
                bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.open_channel_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "openChannel")
                return

            except Exception as e:
                bot.send_message(chat_id=msg.chat_id, text="<b>Provided value is not valid.</b>", reply_markup=self.open_channel_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "openChannel")
                return

        if conv_state == "payinvoice_otp":
            if re.fullmatch("[0-9]{6}", cmd) is not None:
                self.executePayment(msg.from_user.username, msg.chat_id, code_otp=cmd)
            else:
                bot.send_message(chat_id=msg.chat_id, text="This is not 2FA code. Please send 6-digit code or /cancel_payment")
            return

        if conv_state == "openChannel_otp":
            if re.fullmatch("[0-9]{6}", cmd) is not None:
                self.executeOpeningChannel(msg.from_user.username, msg.chat_id, code_otp=cmd)
            else:
                bot.send_message(chat_id= msg.chat_id, text="This is not 2FA code. Please send 6-digit code or /cancel_opening_channel")
            return

        if cmd.lower()[:4] in ["lnbc", "lntb", "lnbcrt"] or cmd.lower()[:10] == "lightning:":  # LN invoice
            value, isValid = self.LNwallet.decodeInvoice(cmd, qr=False)  # isValid = True means it is valid LN invoice
            if isValid:
                msgtext = self.LNwallet.formatDecodedInvoice(value, username)
                self.userdata.set_wallet_payinvoice(msg.from_user.username, value)
                if self.otp_enabled:
                    self.userdata.set_conv_state(username, "payinvoice_otp")
                    bot.send_message(chat_id=msg.chat_id, text=msgtext + "\n<i>send me 2FA code for payment or</i> /cancel_payment", parse_mode=telegram.ParseMode.HTML)
                else:
                    bot.send_message(chat_id=msg.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                    bot.send_message(chat_id=msg.chat_id, text="Do you want to pay this invoice?", reply_markup=self.confirm_menu())
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
            msgtext = self.LNwallet.formatDecodedInvoice(value, msg.from_user.username)
            self.userdata.set_wallet_payinvoice(msg.from_user.username, value)
            if self.otp_enabled:
                self.userdata.set_conv_state(msg.from_user.username, "payinvoice_otp")
                bot.send_message(chat_id=msg.chat_id, text=msgtext + "\n<i>send me 2FA code for payment or</i> /cancel_payment", parse_mode=telegram.ParseMode.HTML)
            else:
                bot.send_message(chat_id=msg.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                bot.send_message(chat_id=msg.chat_id, text="Do you want to pay this invoice?", reply_markup=self.confirm_menu())
        else:
            bot.send_message(chat_id=msg.chat_id, text="I'm sorry " + value + ".🙀")

    # ------------------------------- Command handlers
    @restricted
    def start(self, bot, update):
        msg = update["message"]
        if self.userdata.get_chat_id(msg.from_user.username) is None:
            self.userdata.set_chat_id(msg.from_user.username, msg.chat_id)

        text = "<b>Command List</b>\n"
        for c in self.commands:
            text += "/" + c
        text += "\n\nTo pay invoice just send picture of a QR code or directly paste invoice text in chat."
        bot.send_message(chat_id=msg.chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

    @restricted
    def select_unit(self, bot, update):
        msg = update["message"]
        bot.send_message(chat_id=msg.chat_id, text="Please select unit you want to use.", reply_markup=self.unit_menu())

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
        bot.send_message(chat_id=msg.chat_id,
                         text="Give me details about invoice or press generate.",
                         reply_markup=self.add_invoice_menu())
        self.userdata.set_conv_state(msg.from_user.username, "createInvoice")

    @restricted
    def cancelPayment(self, bot, update):
        if update["message"] is not None:  # command execution
            msg = update["message"]
            username = msg.from_user.username
            chat_id = msg.chat_id
        else:  # call from callback handler
            username = update.effective_user.username
            chat_id = update.callback_query.message.chat_id

        self.userdata.set_conv_state(username, None)
        invoice_data = self.userdata.get_wallet_payinvoice(username)
        if invoice_data is not None:
            amt = invoice_data["decoded"]["num_satoshis"]
            self.userdata.set_wallet_payinvoice(username, None)
            bot.send_message(chat_id=chat_id, text="Payment of " + formatAmount(int(amt), self.userdata.get_selected_unit(username)) + " cancelled.")
        else:
            bot.send_message(chat_id=chat_id, text="You don't have any invoice to cancel.")

    @restricted
    def cancelOpeningChannel(self, bot, update):
        if update["message"] is not None:  # command execution
            msg = update["message"]
            username = msg.from_user.username
            chat_id = msg.chat_id
        else:  # call from callback handler
            username = update.effective_user.username
            chat_id = update.callback_query.message.chat_id

        self.userdata.delete_open_channel_data(username)
        self.userdata.set_conv_state(username, None)
        bot.send_message(chat_id=chat_id, text="Opening new channel canceled.")

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
        bot.send_message(chat_id=msg.chat_id, text="Select address type. If you are unsure select Compatibility.", reply_markup=self.new_address_menu())

    @restricted
    def walletBalance(self, bot, update):
        msg = update["message"]

        bot.send_chat_action(chat_id=msg.chat_id, action=telegram.ChatAction.TYPING)
        ret, err = self.LNwallet.getBalance()
        if err is None:
            bot.send_message(chat_id=msg.chat_id, text=self.LNwallet.formatBalanceOutput(ret, msg.from_user.username), parse_mode=telegram.ParseMode.HTML)
        else:
            bot.send_message(chat_id=msg.chat_id, text="Error at acquiring balance report.")

    @restricted
    def listChannels(self, bot, update):
        msg = update["message"]
        self.getChannelsPage(msg.from_user.username, msg.chat_id, 0)

    @restricted
    def openChannel(self, bot, update):
        msg = update["message"]
        self.userdata.delete_open_channel_data(msg.from_user.username)
        bot.send_message(chat_id=msg.chat_id,
                         text="Enter <i>Node URI</i> and <i>Amount</i>, everything else is optional.",
                         reply_markup=self.open_channel_menu(), parse_mode=telegram.ParseMode.HTML)
        self.userdata.set_conv_state(msg.from_user.username, "openChannel")

if __name__ == "__main__":
    nodebot = Bot(otp=False)
    nodebot.run()
