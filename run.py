import telegram
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackQueryHandler
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from functools import wraps
import logging
import os
import pyqrcode, pyotp
import threading
import uuid
from helper import logToFile, build_menu, amount_parse, formatAmount, parse_bip21, parse_config, update_config_whitelist
from wallet import Wallet
import re
from pyzbar.pyzbar import decode
from PIL import Image
from userdata import UserData


def restricted(func):
    @wraps(func)
    def wrapped(self, bot, update, *args, **kwargs):
        try:
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
        except Exception as e:
            logToFile("Exception restricted: " + str(e))
            return
    return wrapped


class Bot:

    root_dir = os.path.dirname(os.path.abspath(__file__))
    config_file_path = os.path.join(root_dir, "private", "btcnodebot.conf")
    access_whitelist_user = []

    def __init__(self):
        self.config = parse_config(self.config_file_path)
        self.otp_enabled = self.config["bototp"]
        self.access_whitelist_user = self.config["botwhitelist"]
        self.updater = None
        if self.config["bottoken"] == "":
            text = "No bot token found in btcnodebot.conf. Please use @BotFather to create telegram bot and acquire token."
            print(text)
            logToFile(text)
            return

        temp_path = os.path.join(self.root_dir, "temp")
        if not os.path.exists(temp_path):
            os.mkdir(temp_path)

        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
        self.logger = logging.getLogger(__name__)

        with open(self.root_dir + "/list_of_commands.txt", "r") as file:
            self.commands = file.readlines()

        self.updater = Updater(token=self.config["bottoken"], request_kwargs={'read_timeout': 6})
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
        btc_exp_handler = CommandHandler('block_explorer', self.select_explorer)
        self.dispatcher.add_handler(btc_exp_handler)
        notif_handler = CommandHandler('notifications', self.notifications_toggle)
        self.dispatcher.add_handler(notif_handler)
        walletCancelPayHandler = CommandHandler('cancel_payment', self.cancelPayment)
        self.dispatcher.add_handler(walletCancelPayHandler)
        walletOnchainAddressHandler = CommandHandler('onchain_addr', self.walletOnchainAddress)
        self.dispatcher.add_handler(walletOnchainAddressHandler)
        walletOnchainSendHandler = CommandHandler('onchain_send', self.walletOnchainSend)
        self.dispatcher.add_handler(walletOnchainSendHandler)
        walletCancelOnchainTxHandler = CommandHandler('cancel_transaction', self.cancelOnchainTx)
        self.dispatcher.add_handler(walletCancelOnchainTxHandler)
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
        walletCancelCloseChHandler = CommandHandler('cancel_channel_closing', self.cancelClosingChannel)
        self.dispatcher.add_handler(walletCancelCloseChHandler)

        if self.otp_enabled is True:
            # generate new 2fA secret if doesn't exist
            if not os.path.isfile(self.root_dir + "/private/OTP.txt"):
                secret = pyotp.random_base32()
                with open(self.root_dir + "/private/OTP.txt", "w") as file:
                    file.write(secret)
                pr_uri = pyotp.totp.TOTP(secret).provisioning_uri("BtcNodeBot")
                qr = pyqrcode.create(pr_uri)
                qr.png(self.root_dir + "/private/OTP.png", scale=5)

        # init user data
        self.userdata = UserData(self.access_whitelist_user)

        # init wallet
        self.LNwallet = Wallet(self.updater.bot, self.userdata, self.config)

    def run(self):
        if self.updater is None:
            return
        self.updater.start_polling()
        logToFile("telegram bot online")

    def stop(self):
        self.updater.stop()
        logToFile("telegram bot message listening stopped")

    # ------------------------------- Keyboard Menus
    def notif_menu(self, username):
        notif_state = self.userdata.get_notifications_state(username)
        state1 = "  ✔" if notif_state["node"] else ""
        state2 = "  ✔" if notif_state["transactions"] else ""
        state3 = "  ✔" if notif_state["invoices"] else ""
        button_list = [
            InlineKeyboardButton("Node status (offline, not synced)"+state1, callback_data="notif_node"),
            InlineKeyboardButton("On-Chain transactions"+state2, callback_data="notif_transactions"),
            InlineKeyboardButton("Received LN payments"+state3, callback_data="notif_invoices")
        ]
        notif_menu = build_menu(button_list, n_cols=1)
        return InlineKeyboardMarkup(notif_menu)

    def unit_menu(self):
        button_list = [
            InlineKeyboardButton("Bitcoin (BTC)", callback_data="unit_BTC"),
            InlineKeyboardButton("Millibitcoin (mBTC)", callback_data="unit_mBTC"),
            InlineKeyboardButton("Microbitcoin (bits)", callback_data="unit_bits"),
            InlineKeyboardButton("Satoshis (sats)", callback_data="unit_sats")
        ]
        unit_menu = build_menu(button_list, n_cols=1)
        return InlineKeyboardMarkup(unit_menu)

    def explorer_menu(self):
        button_list = []
        for key, value in self.LNwallet.get_available_explorers().items():
            button_list.append(InlineKeyboardButton(key, callback_data="exp_"+key))

        explorer_menu = build_menu(button_list, n_cols=1)
        return InlineKeyboardMarkup(explorer_menu)

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

    def send_onchain_menu(self):
        button_list = [
            InlineKeyboardButton("Address", callback_data="onchsend_addr"),
            InlineKeyboardButton("Amount", callback_data="onchsend_amt"),
            InlineKeyboardButton("Fee", callback_data="onchsend_fee"),
            InlineKeyboardButton("Target Conf", callback_data="onchsend_tconf"),
            InlineKeyboardButton("Send", callback_data="onchsend_send")
        ]
        send_onchain_menu = build_menu(button_list, n_cols=2)
        return InlineKeyboardMarkup(send_onchain_menu)

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

    def close_channel_button(self, chan_id):
        button_list = [
            InlineKeyboardButton("Close channel", callback_data="closech_" + chan_id)
        ]
        close_channel_button = build_menu(button_list, n_cols=1)
        return InlineKeyboardMarkup(close_channel_button)

    def close_channel_menu(self):
        button_list = [
            InlineKeyboardButton("Target Conf", callback_data="closech_tconf"),
            InlineKeyboardButton("Fee", callback_data="closech_fee"),
            InlineKeyboardButton("Close channel ->", callback_data="closech_execute")
        ]
        close_channel_menu = build_menu(button_list, n_cols=2)
        return InlineKeyboardMarkup(close_channel_menu)

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
    def executeOnchainTx(self, username, chat_id, code_otp=""):
        data = self.userdata.get_onchain_send_data(username)
        onchtxthread = threading.Thread(
            target=self.LNwallet.sendCoins,
            args=[self.updater.bot, chat_id, username, data["address"], data["amount"],
                  data["sat_per_byte"], data["target_conf"], code_otp
                  ]
        )
        onchtxthread.start()
        self.userdata.delete_onchain_send_data(username)
        self.userdata.set_conv_state(username, None)

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

    def executeClosingChannel(self, username, chat_id, code_otp=""):
        data = self.userdata.get_close_channel_data(username)
        closechthread = threading.Thread(
            target=self.LNwallet.closeChannel,
            args=[self.updater.bot, chat_id, username, data["chan_id"], data["target_conf"],
                  data["sat_per_byte"], code_otp
                  ]
        )
        closechthread.start()
        self.userdata.delete_close_channel_data(username)
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
        username = update.effective_user.username
        try:
            param = query.data.split('_')
            bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)

            if param[0] == "unit":
                self.userdata.set_selected_unit(username, param[1])
                bot.send_message(chat_id=query.message.chat_id, text=param[1]+" selected")

            elif param[0] == "exp":
                explorerLink = self.LNwallet.get_available_explorers()[param[1]]
                self.userdata.set_default_explorer(username, explorerLink)
                bot.send_message(chat_id=query.message.chat_id, text=param[1] + " has been set successfully.")

            elif param[0] == "notif":
                self.userdata.toggle_notifications_state(username, param[1])
                bot.send_message(chat_id=query.message.chat_id, text="Notifications settings updated.", reply_markup=self.notif_menu(username))

            elif param[0] == "payment":
                if param[1] == "yes":
                    self.executePayment(username, query.message.chat_id)
                elif param[1] == "no":
                    self.cancelPayment(bot, update)

            elif param[0] == "addinvoice":
                if param[1] == "amt":
                    self.userdata.set_conv_state(username, "createInvoice_amount")
                    bot.send_message(chat_id=query.message.chat_id, text="Write amount in supported units (BTC, mBTC, bits, sats). Examples: 1.5BTC 20,4bits 45 000 000sats 56000sats\nIf there is no unit present, selected unit is assumed.")
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

            elif param[0] == "onchsend":
                if param[1] == "amt":
                    self.userdata.set_conv_state(username, "onchainSend_amount")
                    bot.send_message(chat_id=query.message.chat_id, text="Write amount in supported units (BTC, mBTC, bits, sats). Examples: 1.5BTC 20,4bits 45 000 000sats 56000sats\nIf there is no unit present, selected unit is assumed.")
                elif param[1] == "addr":
                    self.userdata.set_conv_state(username, "onchainSend_address")
                    bot.send_message(chat_id=query.message.chat_id, text="Send picture of a QR code or enter bitcoin address.")
                elif param[1] == "fee":
                    self.userdata.set_conv_state(username, "onchainSend_fee")
                    bot.send_message(chat_id=query.message.chat_id, text="Enter fee in sat/byte.")
                elif param[1] == "tconf":
                    self.userdata.set_conv_state(username, "onchainSend_tconf")
                    bot.send_message(chat_id=query.message.chat_id, text="Enter the target number of blocks that transaction should be confirmed by.")
                elif param[1] == "send":
                    self.userdata.set_conv_state(username, "onchainSend_send")
                    data = self.userdata.get_onchain_send_data(username)
                    if data["address"] == "" or data["amount"] <= 0:
                        self.updater.bot.send_message(chat_id=query.message.chat_id, text="Sending failed, Address and Amount are required.")
                        self.userdata.delete_onchain_send_data(username)
                        self.userdata.set_conv_state(username, None)
                    else:
                        msgtext = self.LNwallet.formatOnchainTxOutput(self.userdata.get_onchain_send_data(username), username)
                        if self.otp_enabled:
                            self.userdata.set_conv_state(username, "onchainSend_otp")
                            bot.send_message(chat_id=query.message.chat_id, text=msgtext + "\n\n<i>send me 2FA code or</i> /cancel_transaction", parse_mode=telegram.ParseMode.HTML)
                        else:
                            bot.send_message(chat_id=query.message.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                            bot.send_message(chat_id=query.message.chat_id, text="Do you want to send this transaction?", reply_markup=self.confirm_menu(type="onchsend"))
                elif param[1] == "yes":
                    self.executeOnchainTx(username, query.message.chat_id)
                elif param[1] == "no":
                    self.cancelOnchainTx(bot, update)

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
                        bot.send_message(chat_id=query.message.chat_id, text=formated, reply_markup=self.close_channel_button(ch_data["chan_id"]), parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)
                    page = self.userdata.get_pagination(username)
                    self.getChannelsPage(username, query.message.chat_id, page)

            elif param[0] == "closech":
                if param[1] == "tconf":
                    self.userdata.set_conv_state(username, "closeChannel_tconf")
                    bot.send_message(chat_id=query.message.chat_id, text="Enter the target number of blocks that the commitment transaction should be confirmed by.")
                elif param[1] == "fee":
                    self.userdata.set_conv_state(username, "closeChannel_fee")
                    bot.send_message(chat_id=query.message.chat_id, text="Enter fee in sat/byte, for commitment transaction.")
                elif param[1] == "execute":
                    self.userdata.set_conv_state(username, "closeChannel_execute")
                    closing_data = self.userdata.get_close_channel_data(username)
                    if closing_data["chan_id"] == "":
                        self.updater.bot.send_message(chat_id=query.message.chat_id, text="Channel closing failed, no ChanID.")
                        self.userdata.delete_close_channel_data(username)
                        self.userdata.set_conv_state(username, None)
                    else:
                        channel_data, error = self.LNwallet.getChannelData(closing_data["chan_id"])
                        if error is not None:
                            self.updater.bot.send_message(chat_id=query.message.chat_id, text="Channel closing failed, " + error)
                            self.userdata.delete_close_channel_data(username)
                            self.userdata.set_conv_state(username, None)
                            return
                        msgtext = self.LNwallet.formatChannelCloseOutput(channel_data, closing_data, username)
                        if self.otp_enabled:
                            self.userdata.set_conv_state(username, "closeChannel_otp")
                            bot.send_message(chat_id=query.message.chat_id, text=msgtext + "\n\n<i>send me 2FA code to close or</i> /cancel_channel_closing", parse_mode=telegram.ParseMode.HTML)
                        else:
                            bot.send_message(chat_id=query.message.chat_id, text=msgtext, parse_mode=telegram.ParseMode.HTML)
                            bot.send_message(chat_id=query.message.chat_id, text="Do you want to close this channel?", reply_markup=self.confirm_menu(type="closech"))
                elif param[1] == "yes":
                    self.executeClosingChannel(username, query.message.chat_id)
                elif param[1] == "no":
                    self.cancelClosingChannel(bot, update)
                else:
                    self.userdata.delete_close_channel_data(username)
                    self.userdata.set_conv_state(username, "closeChannel")
                    self.userdata.set_close_channel_data(username, "chan_id", param[1])
                    bot.send_message(chat_id=query.message.chat_id, text="Enter details of commitment transaction or press close channel.", reply_markup=self.close_channel_menu())

            elif param[0] == "opench":
                if param[1] == "addr":
                    self.userdata.set_conv_state(username, "openChannel_addr")
                    bot.send_message(chat_id=query.message.chat_id, text="Send picture of a QR code or enter node's publickey@host.")
                elif param[1] == "lamount":
                    self.userdata.set_conv_state(username, "openChannel_lamount")
                    bot.send_message(chat_id=query.message.chat_id, text="Write amount in supported units (BTC, mBTC, bits, sats). Examples: 1.5BTC 20,4bits 45 000 000sats 56000sats\nIf there is no unit present, selected unit is assumed.")
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

        except Exception as e:
            logToFile("Exception callback_handle: " + str(e))

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
                    value, values_valid = amount_parse(cmd, self.userdata.get_selected_unit(username))
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
                    respText = "Success. Give me details about invoice or press generate."
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
                    value, values_valid = amount_parse(cmd, self.userdata.get_selected_unit(username))
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
                    respText = "Success. Enter <i>Node URI</i> and <i>Amount</i>, everything else is optional."
                else:
                    respText = "<b>Provided value is not valid.</b>"
                bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.open_channel_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "openChannel")
                return

            except Exception as e:
                bot.send_message(chat_id=msg.chat_id, text="<b>Provided value is not valid.</b>", reply_markup=self.open_channel_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "openChannel")
                return

        # --------- closing channel
        if conv_state in ["closeChannel_tconf", "closeChannel_fee"]:
            try:
                values_valid = True
                if conv_state == "closeChannel_tconf":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_close_channel_data(username, "target_conf", value)
                    else:
                        values_valid = False
                elif conv_state == "closeChannel_fee":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_close_channel_data(username, "sat_per_byte", value)
                    else:
                        values_valid = False

                if values_valid:
                    respText = "Success. Enter details of commitment transaction or press close channel."
                else:
                    respText = "<b>Provided value is not valid.</b>"
                bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.close_channel_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "closeChannel")
                return

            except Exception as e:
                bot.send_message(chat_id=msg.chat_id, text="<b>Provided value is not valid.</b>", reply_markup=self.close_channel_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "closeChannel")
                return

        # --------- sending on-chain transaction
        if conv_state in ["onchainSend_amount", "onchainSend_address", "onchainSend_fee", "onchainSend_tconf"]:
            try:
                values_valid = True
                if conv_state == "onchainSend_address":
                    self.userdata.set_onchain_send_data(username, "address", str(cmd))
                elif conv_state == "onchainSend_amount":
                    value, values_valid = amount_parse(cmd, self.userdata.get_selected_unit(username))
                    self.userdata.set_onchain_send_data(username, "amount", value)
                elif conv_state == "onchainSend_fee":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_onchain_send_data(username, "sat_per_byte", value)
                    else:
                        values_valid = False
                elif conv_state == "onchainSend_tconf":
                    value = int(cmd)
                    if value > 0:
                        self.userdata.set_onchain_send_data(username, "target_conf", value)
                    else:
                        values_valid = False

                if values_valid:
                    respText = "Success. Enter details about transaction."
                else:
                    respText = "<b>Provided value is not valid.</b>"
                bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.send_onchain_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "onchainSend")
                return

            except Exception as e:
                bot.send_message(chat_id=msg.chat_id, text="<b>Provided value is not valid.</b>", reply_markup=self.send_onchain_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(msg.from_user.username, "onchainSend")
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

        if conv_state == "closeChannel_otp":
            if re.fullmatch("[0-9]{6}", cmd) is not None:
                self.executeClosingChannel(msg.from_user.username, msg.chat_id, code_otp=cmd)
            else:
                bot.send_message(chat_id=msg.chat_id, text="This is not 2FA code. Please send 6-digit code or /cancel_channel_closing")
            return

        if conv_state == "onchainSend_otp":
            if re.fullmatch("[0-9]{6}", cmd) is not None:
                self.executeOnchainTx(msg.from_user.username, msg.chat_id, code_otp=cmd)
            else:
                bot.send_message(chat_id=msg.chat_id, text="This is not 2FA code. Please send 6-digit code or /cancel_transaction")
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
            if (len(params) == 3 and self.otp_enabled is False) or (len(params) == 4 and self.otp_enabled):
                if self.otp_enabled:
                    if not self.LNwallet.check_otp(params[3]):
                        bot.send_message(chat_id=msg.chat_id, text="2FA code not valid")
                        return
                par_username = params[2]
                if par_username not in self.access_whitelist_user:
                    self.access_whitelist_user.append(par_username)
                    self.userdata.add_new_user(par_username)
                    update_config_whitelist(self.config_file_path, self.access_whitelist_user)
                    bot.send_message(chat_id=msg.chat_id, text=par_username + " added to whitelist")
                else:
                    bot.send_message(chat_id=msg.chat_id, text=par_username + " already in whitelist")

        elif "REMOVE WHITELIST" in cmd.upper():
            params = cmd.split(' ')
            if (len(params) == 3 and self.otp_enabled is False) or (len(params) == 4 and self.otp_enabled):
                if self.otp_enabled:
                    if not self.LNwallet.check_otp(params[3]):
                        bot.send_message(chat_id=msg.chat_id, text="2FA code not valid")
                        return
                par_username = params[2]
                if par_username in self.access_whitelist_user:
                    self.access_whitelist_user.remove(par_username)
                    self.userdata.remove_user(par_username)
                    update_config_whitelist(self.config_file_path, self.access_whitelist_user)
                    bot.send_message(chat_id=msg.chat_id, text=par_username + " removed from whitelist")
                else:
                    bot.send_message(chat_id=msg.chat_id, text=par_username + " not in whitelist")

    @restricted
    def image_handle(self, bot, update):
        msg = update["message"]
        username = msg.from_user.username
        conv_state = self.userdata.get_conv_state(username)

        # download file
        newFile = bot.get_file(update.message.photo[-1].file_id)
        file_ext = newFile.file_path[newFile.file_path.rfind('.'):]
        temp_filename_local = str(uuid.uuid4().hex) + file_ext
        temp_path_local = os.path.join(self.root_dir, "temp", temp_filename_local)
        newFile.download(temp_path_local)

        # --------- opening channel QR code in image
        if conv_state == "openChannel_addr":
            ret = decode(Image.open(temp_path_local))
            if len(ret) > 0:
                text = ret[0].data.decode("utf-8")
                self.userdata.set_open_channel_data(username, "address", str(text))
                bot.send_message(chat_id=msg.chat_id, text=str(text))
                respText = "Success. Enter <i>Node URI</i> and <i>Amount</i>, everything else is optional."
            else:
                respText = "<b>Cannot find and decode QR code.</b>"

            if os.path.exists(temp_path_local):
                os.remove(temp_path_local)
            bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.open_channel_menu(), parse_mode=telegram.ParseMode.HTML)
            self.userdata.set_conv_state(msg.from_user.username, "openChannel")
            return

        # --------- sending on-chain transaction QR code in image
        try:
            if conv_state == "onchainSend_address":
                ret = decode(Image.open(temp_path_local))
                if len(ret) > 0:
                    text = ret[0].data.decode("utf-8")
                    response = parse_bip21(uri=text)
                    if response is not None:
                        self.userdata.set_onchain_send_data(username, "address", response["address"])
                        if "amount_sat" in response:
                            self.userdata.set_onchain_send_data(username, "amount", response["amount_sat"])
                            amt = formatAmount(int(response["amount_sat"]), self.userdata.get_selected_unit(username))
                            bot.send_message(chat_id=msg.chat_id, text=response["address"] + "\nAmount: " + amt)
                            respText = "<i>Address</i> and <i>Amount</i> have been set successfully."
                        else:
                            bot.send_message(chat_id=msg.chat_id, text=response["address"])
                            respText = "<i>Address</i> has been set successfully."
                    elif "req-" in text:
                        respText = "<b>Bitcoin URI is invalid.</b>"
                    else:
                        self.userdata.set_onchain_send_data(username, "address", text)  # only address not bip21 uri
                        bot.send_message(chat_id=msg.chat_id, text=text)
                        respText = "<i>Address</i> has been set successfully."
                else:
                    respText = "<b>Cannot find and decode QR code.</b>"

                bot.send_message(chat_id=msg.chat_id, text=respText, reply_markup=self.send_onchain_menu(), parse_mode=telegram.ParseMode.HTML)
                self.userdata.set_conv_state(username, "onchainSend")
                if os.path.exists(temp_path_local):
                    os.remove(temp_path_local)
                return
        except Exception as e:
            bot.send_message(chat_id=msg.chat_id, text="<b>Cannot find and decode QR code.</b>", reply_markup=self.send_onchain_menu(), parse_mode=telegram.ParseMode.HTML)
            self.userdata.set_conv_state(username, "onchainSend")
            return

        # --------- LN invoice QR code in image
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
        text += "\n\nTo pay LN invoice just send picture of a QR code or directly paste invoice text in chat."
        bot.send_message(chat_id=msg.chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

    @restricted
    def select_unit(self, bot, update):
        msg = update["message"]
        bot.send_message(chat_id=msg.chat_id, text="Please select unit you want to use.", reply_markup=self.unit_menu())

    @restricted
    def select_explorer(self, bot, update):
        msg = update["message"]
        bot.send_message(chat_id=msg.chat_id, text="Please select block explorer you want to use, when opening Tx links.", reply_markup=self.explorer_menu())

    @restricted
    def notifications_toggle(self, bot, update):
        msg = update["message"]
        bot.send_message(chat_id=msg.chat_id, text="Enable or disable notifications.", reply_markup=self.notif_menu(msg.from_user.username))

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
    def cancelClosingChannel(self, bot, update):
        if update["message"] is not None:  # command execution
            msg = update["message"]
            username = msg.from_user.username
            chat_id = msg.chat_id
        else:  # call from callback handler
            username = update.effective_user.username
            chat_id = update.callback_query.message.chat_id

        data = self.userdata.get_close_channel_data(username)
        if data["chan_id"] == "":
            bot.send_message(chat_id=chat_id, text="No channel to cancel.")
            return

        self.userdata.delete_close_channel_data(username)
        self.userdata.set_conv_state(username, None)
        bot.send_message(chat_id=chat_id, text="Channel closing canceled.")

    @restricted
    def cancelOpeningChannel(self, bot, update):
        if update["message"] is not None:  # command execution
            msg = update["message"]
            username = msg.from_user.username
            chat_id = msg.chat_id
        else:  # call from callback handler
            username = update.effective_user.username
            chat_id = update.callback_query.message.chat_id

        data = self.userdata.get_open_channel_data(username)
        if data["address"] == "" or data["local_amount"] <= 0:
            bot.send_message(chat_id=chat_id, text="No channel to cancel.")
            return

        self.userdata.delete_open_channel_data(username)
        self.userdata.set_conv_state(username, None)
        bot.send_message(chat_id=chat_id, text="Opening new channel canceled.")

    @restricted
    def cancelOnchainTx(self, bot, update):
        if update["message"] is not None:  # command execution
            msg = update["message"]
            username = msg.from_user.username
            chat_id = msg.chat_id
        else:  # call from callback handler
            username = update.effective_user.username
            chat_id = update.callback_query.message.chat_id

        data = self.userdata.get_onchain_send_data(username)
        if data["address"] == "" or data["amount"] <= 0:
            bot.send_message(chat_id=chat_id, text="No transaction to cancel.")
            return

        self.userdata.delete_onchain_send_data(username)
        self.userdata.set_conv_state(username, None)
        bot.send_message(chat_id=chat_id, text="Sending transaction canceled.")

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
    def walletOnchainSend(self, bot, update):
        msg = update["message"]
        self.userdata.delete_onchain_send_data(msg.from_user.username)
        self.userdata.set_conv_state(msg.from_user.username, "onchainSend")
        bot.send_message(chat_id=msg.chat_id, text="Enter details about transaction.",
                         reply_markup=self.send_onchain_menu(), parse_mode=telegram.ParseMode.HTML)

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
    btcnodebot = Bot()
    btcnodebot.run()
