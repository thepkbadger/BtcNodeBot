from pyzbar.pyzbar import decode
from PIL import Image
import os
from helper import logToFile
from datetime import timedelta, datetime, timezone
from node.local_node import LocalNode
import pyotp
import threading
from time import sleep


class Wallet:

    root_path = os.path.dirname(os.path.abspath(__file__))
    explorerTxLink = {
        "blockstream.info": "https://blockstream.info/tx/",
        "oxt.me": "https://oxt.me/transaction/",
        "blockcypher.com": "https://live.blockcypher.com/btc/tx/",
        "blockchair.com": "https://blockchair.com/bitcoin/transaction/",
        "blockchain.com": "https://www.blockchain.com/btc/tx/"
    }

    def __init__(self, bot, userdata, node_conn="local", unit="sats", enable_otp=False):
        self.bot = bot
        self.enable_otp = enable_otp
        self.userdata = userdata
        self.threadList = []
        self.unit = unit  # units: sats, mBTC, BTC
        self.node = LocalNode()  # TODO arguments
        self.subscribe_notifications()

    def get_available_explorers(self):
        return self.explorerTxLink

    def subscribe_notifications(self):
        subscriptions = [
            self.node.subscribe_node_watcher,
            self.node.subscribe_invoices,
            self.node.subscribe_transactions
        ]
        for subscription in subscriptions:
            t = threading.Thread(target=subscription, args=[self.bot, self.userdata])
            self.threadList.append(t)
            t.start()
            sleep(0.1)

    def check_otp(self, code):
        if self.enable_otp is False:
            return True
        with open(self.root_path + "/private/OTP.txt", "r") as file:
            secret = file.readline()
            totp = pyotp.TOTP(secret)
            if code == totp.now():
                return True
        return False

    def decodeInvoice(self, input_data, qr=True):
        try:
            if qr:
                ret = decode(Image.open(os.path.join(self.root_path, "temp", input_data)))
                if len(ret) > 0:
                    text = ret[0].data.decode("utf-8")
                else:
                    return "can't find and decode QR code", False
            else:
                text = input_data.strip()

            # remove uri "lightning:" from start of text
            if text.lower()[:10] == "lightning:":
                text = text[10:]

            decoded_data, error = self.node.decode_ln_invoice(pay_req=text)
            if error is None:
                info_data, error_info = self.node.get_ln_node_info(pub_key=decoded_data["destination"])  # get destination node info, alias...
                ret_data = {"decoded": decoded_data, "destination_node": info_data, "raw_invoice": text}
                return ret_data, True
            return "this is not valid invoice", False

        except Exception as e:
            logToFile("Exception at decoding invoice input data: "+str(e))
            return "there was error at decoding", False

    def formatDecodedInvoice(self, data, lb_symbol="\n"):

        d_time = datetime.fromtimestamp(int(data["decoded"]["timestamp"]), timezone.utc)
        expiry = int(data["decoded"]["expiry"])
        d_time_expiration = d_time + timedelta(seconds=expiry)

        to = data["destination_node"]["node"]["alias"] if data["destination_node"] else data["decoded"]["destination"]
        return "<b>Lightning invoice: </b>" + lb_symbol \
            + "To: " + to + lb_symbol \
            + "Amount: " + data["decoded"]["num_satoshis"] + " sats" + lb_symbol \
            + "Expiration: " + d_time_expiration.strftime('%d.%m.%Y %H:%M:%S %z') + lb_symbol \
            + "Description: " + data["decoded"]["description"] + lb_symbol

    def payInvoice(self, pay_req, bot, chat_id, username, otp_code=""):
        try:
            sending_msg = bot.send_message(chat_id=chat_id, text="Sending payment...")
            if self.check_otp(otp_code) is False:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't pay invoice, 2FA code not valid.")
                return

            out_json, error = self.node.pay_ln_invoice(pay_req)
            if error is None:
                if out_json["payment_error"] != "":
                    bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't pay invoice, " + str(out_json["payment_error"]))
                    return
                else:
                    # as soon as we know payment was successful, clear invoice from user data
                    self.userdata.set_wallet_payinvoice(username, None)
                    if self.userdata.get_conv_state(username) == "payinvoice_otp":
                        self.userdata.set_conv_state(username, None)

                    total_amt = out_json["payment_route"]["total_amt"]
                    num_hops = len(out_json["payment_route"]["hops"])
                    if "total_fees_msat" in out_json["payment_route"]:
                        total_fees = out_json["payment_route"]["total_fees_msat"]
                    else:
                        total_fees = 0
                    msg_text = "<b>Invoice has been paid.</b>\n" \
                               + "Total amount: " + "{:,}".format(int(total_amt)).replace(',', '.') + " sats\n" \
                               + "Total fees: " + "{:,}".format(int(total_fees)).replace(',', '.') + " msats\n" \
                               + "hops: " + str(num_hops) + "\n"
                    import telegram
                    bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text=msg_text, parse_mode=telegram.ParseMode.HTML)
                    return

            bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't pay invoice, " + str(error))
        except Exception as e:
            text = str(e)
            logToFile("Exception payInvoice wallet: " + text)
            bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't pay invoice, there was an error.")

    def addInvoice(self, memo="", value=0, expiry=3600):
        return self.node.add_ln_invoice(value, memo, expiry)

    def getInfo(self):
        return self.node.get_ln_info()

    def getChannels(self, page=-1, per_page=-1):
        channels, err = self.node.get_channel_list()
        if err is not None:
            return None, err
        if page < 0 or per_page < 0:
            return channels, None
        try:
            response = {"channels": [], "last": False}
            num_of_channels = len(channels["channels"])
            start = page * per_page
            i = 0
            while i < per_page:
                if (start + i) >= num_of_channels:
                    return None, "no more pages."
                ch_data = channels["channels"][i+start]
                info_data, error_info = self.node.get_ln_node_info(pub_key=ch_data["remote_pubkey"])
                if info_data is None:
                    alias = ch_data["remote_pubkey"][:12]
                else:
                    alias = info_data["node"]["alias"]
                ch_data["alias"] = alias
                response["channels"].append(ch_data)
                i += 1
                if (start + i) >= num_of_channels:
                    response["last"] = True
                    break

            return response, None
        except Exception as e:
            text = str(e)
            logToFile("Exception getChannels: " + text)
            return None, text

    def getChannelData(self, chan_id):
        channels, err = self.node.get_channel_list()
        if err is not None:
            return None, err

        for ch in channels["channels"]:
            if ch["chan_id"] == chan_id:
                info_data, error_info = self.node.get_ln_node_info(pub_key=ch["remote_pubkey"])
                if info_data is None:
                    ch["alias"] = ch["remote_pubkey"][:12]
                else:
                    ch["alias"] = info_data["node"]["alias"]
                return ch, None
        return None, "channel not found."

    def getOnchainAddress(self, type="p2wkh"):
        try:
            out_json, error = self.node.get_ln_onchain_address(addr_type=type)
            if error is None:
                return out_json["address"], None
            return None, error
        except Exception as e:
            text = str(e)
            logToFile("Exception getOnchainAddress: " + text)
            return None, text

    def getBalance(self):
        try:
            response, error = self.node.get_balance_report()
            if error is None:
                balance = {
                    "onchain_total": response["onchain"]["total_balance"],
                    "onchain_confirmed": response["onchain"]["confirmed_balance"],
                    "onchain_unconfirmed": response["onchain"]["unconfirmed_balance"],
                    "num_channels": len(response["ln"]["channels"]),
                    "num_active": 0,
                    "channels": {
                        "effective_outbound_capacity": 0,
                        "effective_inbound_capacity": 0,
                        "outbound_capacity": 0,
                        "inbound_capacity": 0,
                        "inactive_aliases": []
                    }
                }
                num_active = 0
                for channel in response["ln"]["channels"]:
                    balance["channels"]["outbound_capacity"] += int(channel["local_balance"])
                    balance["channels"]["inbound_capacity"] += int(channel["remote_balance"])

                    if channel["active"]:
                        num_active += 1
                        balance["channels"]["effective_outbound_capacity"] += int(channel["local_balance"])
                        balance["channels"]["effective_inbound_capacity"] += int(channel["remote_balance"])
                    else:
                        info_data, error_info = self.node.get_ln_node_info(pub_key=channel["remote_pubkey"])
                        if info_data is None:
                            balance["channels"]["inactive_aliases"].append(channel["remote_pubkey"][:12])
                        else:
                            balance["channels"]["inactive_aliases"].append(info_data["node"]["alias"])

                balance["num_active"] = num_active
                return balance, None
            return None, error
        except Exception as e:
            text = str(e)
            logToFile("Exception at getBalance: "+text)
            return None, text

    def formatBalanceOutput(self, data, lb_symbol="\n"):
        args = [
            data["onchain_total"], data["onchain_confirmed"], data["onchain_unconfirmed"],
            data["channels"]["outbound_capacity"], data["channels"]["inbound_capacity"]
        ]

        text = "<i>🔗 On-chain:</i>" + lb_symbol \
            + "Total: {0}" + lb_symbol + "Confirmed: {1}" + lb_symbol + "Unconfirmed: {2}" + lb_symbol + lb_symbol \
            + "<i>⚡ Lightning channels: ("+str(data["num_active"])+"/"+str(data["num_channels"])+")</i>" + lb_symbol \
            + "<i>--Capacities</i>" + lb_symbol \
            + "Local: {3}" + lb_symbol \
            + "Remote: {4}"

        if len(data["channels"]["inactive_aliases"]) > 0:
            args.append(data["channels"]["effective_outbound_capacity"])
            args.append(data["channels"]["effective_inbound_capacity"])
            text += lb_symbol \
                + "Effective Local: {5}" + lb_symbol \
                + "Effective Remote: {6}" + lb_symbol \
                + "<i>--Inactive channels</i>" + lb_symbol \
                + ", ".join(data["channels"]["inactive_aliases"])

        if self.unit == "BTC":
            for idx, arg in enumerate(args):
                args[idx] = "0" + " " + self.unit if arg == 0 else str("%.8f" % (arg / 100000000.0)).rstrip('0') + " " + self.unit
        elif self.unit == "mBTC":
            for idx, arg in enumerate(args):
                args[idx] = "0" + " " + self.unit if arg == 0 else str("%.5f" % (arg / 100000.0)).rstrip('0') + " " + self.unit
        else:
            for idx, arg in enumerate(args):
                args[idx] = "{:,}".format(int(arg)).replace(',', '.') + " " + self.unit

        text = text.format(*args)
        return text

    def formatChannelOutput(self, data, explorerLink, lb_symbol="\n"):
        active_text = "" if data["active"] else "  🔴 <i>offline</i>"
        private_text = "private" if data["private"] else "public"
        fund_txid = data["channel_point"][:data["channel_point"].find(':')]
        funding_link = "<a href='" + explorerLink + fund_txid + "'>" + fund_txid[:8] + "..." + fund_txid[-8:] + "</a>"

        text = "<b>" + data["alias"] + "</b>" + active_text + lb_symbol \
            + data["remote_pubkey"] + lb_symbol \
            + "Capacity: {0}" + lb_symbol \
            + "Local Balance: {1}" + lb_symbol \
            + "Remote Balance: {2}" + lb_symbol \
            + "Time Lock: " + str(data["csv_delay"]) + lb_symbol \
            + "Number of Updates: " + data["num_updates"] + lb_symbol \
            + "Total Sent: {3}" + lb_symbol \
            + "Total Received: {4}" + lb_symbol \
            + "Type: " + private_text + lb_symbol \
            + "Funding Tx: " + funding_link

        args = [
            int(data["local_balance"])+int(data["remote_balance"]), int(data["local_balance"]), int(data["remote_balance"]), int(data["total_satoshis_sent"]),
            int(data["total_satoshis_received"])
        ]

        if self.unit == "BTC":
            for idx, arg in enumerate(args):
                args[idx] = "0" + " " + self.unit if arg == 0 else str("%.8f" % (arg / 100000000.0)).rstrip('0') + " " + self.unit
        elif self.unit == "mBTC":
            for idx, arg in enumerate(args):
                args[idx] = "0" + " " + self.unit if arg == 0 else str("%.5f" % (arg / 100000.0)).rstrip('0') + " " + self.unit
        else:
            for idx, arg in enumerate(args):
                args[idx] = "{:,}".format(int(arg)).replace(',', '.') + " " + self.unit

        text = text.format(*args)
        return text
