from pyzbar.pyzbar import decode
from PIL import Image
import os
from helper import logToFile, formatAmount
from datetime import timedelta, datetime, timezone
from node.local_node import LocalNode
import pyotp
import threading
from time import sleep
import telegram
from base64 import b64decode


class Wallet:

    root_path = os.path.dirname(os.path.abspath(__file__))
    explorerTxLink = {
        "blockstream.info": "https://blockstream.info/tx/",
        "oxt.me": "https://oxt.me/transaction/",
        "blockcypher.com": "https://live.blockcypher.com/btc/tx/"
    }

    def __init__(self, bot, userdata, config):
        self.bot = bot
        self.enable_otp = config["bototp"]
        self.userdata = userdata
        self.threadList = []
        self.node = LocalNode(config=config)
        self.subscribe_notifications()

    def get_available_explorers(self):
        return self.explorerTxLink

    def subscribe_notifications(self):
        subscriptions = [
            self.node.subscribe_node_watcher,
            self.node.subscribe_invoices,
            self.node.subscribe_transactions,
            self.node.subscribe_channel_events
        ]
        for subscription in subscriptions:
            t = threading.Thread(target=subscription, args=[self.bot, self.userdata], daemon=True)
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

    def formatDecodedInvoice(self, data, username, lb_symbol="\n"):

        d_time = datetime.fromtimestamp(int(data["decoded"]["timestamp"]), timezone.utc)
        expiry = int(data["decoded"]["expiry"])
        d_time_expiration = d_time + timedelta(seconds=expiry)

        to = data["destination_node"]["node"]["alias"] if data["destination_node"] else data["decoded"]["destination"]
        unit = self.userdata.get_selected_unit(username)
        return "<b>Lightning invoice: </b>" + lb_symbol \
            + "To: " + to + lb_symbol \
            + "Amount: " + formatAmount(int(data["decoded"]["num_satoshis"]), unit) + lb_symbol \
            + "Expiration: " + d_time_expiration.strftime('%c %Z') + lb_symbol \
            + "Description: " + data["decoded"]["description"] + lb_symbol

    def payInvoice(self, pay_req, bot, chat_id, username, otp_code=""):
        sending_msg = bot.send_message(chat_id=chat_id, text="Sending payment...")
        try:
            if self.check_otp(otp_code) is False:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't pay invoice, 2FA code not valid.")
                return

            out_json, error = self.node.pay_ln_invoice(pay_req)
            if error is None:
                if out_json["payment_error"] != "":
                    bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't pay invoice, " + str(out_json["payment_error"]))
                    return
                else:
                    total_amt = out_json["payment_route"]["total_amt"]
                    num_hops = len(out_json["payment_route"]["hops"])
                    if "total_fees_msat" in out_json["payment_route"]:
                        total_fees = out_json["payment_route"]["total_fees_msat"]
                    else:
                        total_fees = 0
                    unit = self.userdata.get_selected_unit(username)
                    msg_text = "<b>Invoice has been paid.</b>\n" \
                               + "Total amount: " + formatAmount(int(total_amt), unit) + "\n" \
                               + "Total fees: " + "{:,}".format(int(total_fees)).replace(',', '.') + " msats\n" \
                               + "hops: " + str(num_hops) + "\n"

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
        if len(channels["channels"]) == 0:
            return None, "no channels."
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

        try:
            for ch in channels["channels"]:
                if ch["chan_id"] == chan_id:
                    info_data, error_info = self.node.get_ln_node_info(pub_key=ch["remote_pubkey"])
                    if info_data is None:
                        ch["alias"] = ch["remote_pubkey"][:12]
                    else:
                        ch["alias"] = info_data["node"]["alias"]
                    return ch, None
            return None, "channel not found."
        except Exception as e:
            text = str(e)
            logToFile("Exception getChannelData: " + text)
            return None, text

    def openChannel(self, bot, chat_id, username, addr, local_funding_amount, target_conf=-1, sat_per_byte=-1, private=False, min_htlc_msat=1000, remote_csv_delay=-1, otp_code=""):
        sending_msg = bot.send_message(chat_id=chat_id, text="Opening channel...")
        try:
            if self.check_otp(otp_code) is False:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't open channel, 2FA code not valid.")
                return

            uri = addr.split('@')
            if len(uri) != 2:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't open channel, Node URI not valid.")
                return
            conn_response, error_conn = self.node.connect_peer(pubkey=uri[0], host=uri[1])
            if error_conn is not None and "already" not in error_conn:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't open channel, " + error_conn)
                return

            open_response, error_open = self.node.open_channel(uri[0], local_funding_amount, private, min_htlc_msat, remote_csv_delay, sat_per_byte, target_conf)
            if error_open is not None:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't open channel, " + error_open)
                return

            explorerLink = self.userdata.get_default_explorer(username)
            fund_txid_bytes = b64decode(open_response["funding_txid_bytes"])[:: -1]  # decode base64 and reverse bytes
            fund_txid = fund_txid_bytes.hex()  # bytes to hex string
            msg_text = "<b>Channel successfully opened.</b>\n" \
                    + "Please wait for funding tx confirmation.\n" \
                    + "Funding Tx: <a href='" + explorerLink + fund_txid + "'>" + fund_txid[:8] + "..." + fund_txid[-8:] + "</a>"
            bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text=msg_text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

        except Exception as e:
            text = str(e)
            logToFile("Exception openChannel wallet: " + text)
            bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't open channel, there was an error.")

    def closeChannel(self, bot, chat_id, username, chan_id, target_conf=-1, sat_per_byte=-1, otp_code=""):
        msg = bot.send_message(chat_id=chat_id, text="Closing channel...")
        try:
            if self.check_otp(otp_code) is False:
                bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="I couldn't close channel, 2FA code not valid.")
                return

            ch, error = self.getChannelData(chan_id)
            if error is not None:
                bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="I couldn't close channel, " + error)
                return

            force = False if ch["active"] else True

            response, error_close = self.node.close_channel(ch["channel_point"], target_conf, sat_per_byte, force)
            if error_close is not None:
                bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="I couldn't close channel, " + error_close)
                return

            explorerLink = self.userdata.get_default_explorer(username)
            commit_txid_bytes = b64decode(response["close_pending"]["txid"])[:: -1]  # decode base64 and reverse bytes
            commit_txid = commit_txid_bytes.hex()  # bytes to hex string
            close_type = "force " if force else ""
            info = str(ch["csv_delay"]) + " blocks, for funds to be available." if force else "for commitment tx confirmation."
            msg_text = "<b>Channel successfully " + close_type + "closed.</b>\n" \
                       + "Please wait " + info + "\n" \
                       + "Commitment Tx: <a href='" + explorerLink + commit_txid + "'>" + commit_txid[:8] + "..." + commit_txid[-8:] + "</a>"
            bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text=msg_text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

        except Exception as e:
            text = str(e)
            logToFile("Exception closeChannel wallet: " + text)
            bot.edit_message_text(chat_id=chat_id, message_id=msg.message_id, text="I couldn't close channel, there was an error.")

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

    def sendCoins(self, bot, chat_id, username, address, amount, sat_per_byte=-1, target_conf=-1, otp_code=""):
        sending_msg = bot.send_message(chat_id=chat_id, text="Sending transaction...")
        try:
            if self.check_otp(otp_code) is False:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't send transaction, 2FA code not valid.")
                return

            response, error = self.node.send_coins(address, amount, sat_per_byte, target_conf)
            if error is not None:
                bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't send transaction, " + str(error))
                return

            explorerLink = self.userdata.get_default_explorer(username)
            txid = response["txid"]
            msg_text = "<b>Transaction broadcasted</b>\n" \
                    + "Txid: <a href='" + explorerLink + txid + "'>" + txid[:8] + "..." + txid[-8:] + "</a>"
            bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text=msg_text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

        except Exception as e:
            text = str(e)
            logToFile("Exception sendCoins: " + text)
            bot.edit_message_text(chat_id=chat_id, message_id=sending_msg.message_id, text="I couldn't send transaction, there was an error.")

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

    def formatBalanceOutput(self, data, username, lb_symbol="\n"):
        args = [
            int(data["onchain_total"]), int(data["onchain_confirmed"]), int(data["onchain_unconfirmed"]),
            int(data["channels"]["outbound_capacity"]), int(data["channels"]["inbound_capacity"])
        ]

        text = "<i>🔗 On-chain:</i>" + lb_symbol \
            + "Total: {0}" + lb_symbol + "Confirmed: {1}" + lb_symbol + "Unconfirmed: {2}" + lb_symbol + lb_symbol \
            + "<i>⚡ Lightning channels: ("+str(data["num_active"])+"/"+str(data["num_channels"])+")</i>" + lb_symbol \
            + "<i>--Capacities</i>" + lb_symbol \
            + "Local: {3}" + lb_symbol \
            + "Remote: {4}"

        if len(data["channels"]["inactive_aliases"]) > 0:
            args.append(int(data["channels"]["effective_outbound_capacity"]))
            args.append(int(data["channels"]["effective_inbound_capacity"]))
            text += lb_symbol \
                + "Effective Local: {5}" + lb_symbol \
                + "Effective Remote: {6}" + lb_symbol \
                + "<i>--Inactive channels</i>" + lb_symbol \
                + ", ".join(data["channels"]["inactive_aliases"])

        unit = self.userdata.get_selected_unit(username)
        for idx, arg in enumerate(args):
            args[idx] = formatAmount(arg, unit)

        text = text.format(*args)
        return text

    def formatChannelOutput(self, data, username, lb_symbol="\n"):
        active_text = "" if data["active"] else "  🔴 <i>offline</i>"
        private_text = "private" if data["private"] else "public"
        fund_txid = data["channel_point"][:data["channel_point"].find(':')]
        explorerLink = self.userdata.get_default_explorer(username)
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

        unit = self.userdata.get_selected_unit(username)
        for idx, arg in enumerate(args):
            args[idx] = formatAmount(arg, unit)

        text = text.format(*args)
        return text

    def formatChannelOpenOutput(self, data, username, lb_symbol="\n"):
        target_conf = data["target_conf"] if data["target_conf"] > 0 else "/"
        fee = data["sat_per_byte"] if data["sat_per_byte"] > 0 else "/"
        csv_delay = data["remote_csv_delay"] if data["remote_csv_delay"] > 0 else "/"
        private = "yes" if data["private"] else "no"

        text = "<b>New channel</b>" + lb_symbol \
            + data["address"] + lb_symbol \
            + "Amount: {0}" + lb_symbol \
            + "Min HTLC: " + str(data["min_htlc_msat"]) + " msats" + lb_symbol \
            + "Time Lock: " + str(csv_delay) + lb_symbol \
            + "Target Conf: " + str(target_conf) + lb_symbol \
            + "Fees(sat/byte): " + str(fee) + lb_symbol \
            + "Private: " + private

        args = [int(data["local_amount"])]

        unit = self.userdata.get_selected_unit(username)
        for idx, arg in enumerate(args):
            args[idx] = formatAmount(arg, unit)

        text = text.format(*args)
        return text

    def formatOnchainTxOutput(self, data, username, lb_symbol="\n"):
        target_conf = data["target_conf"] if data["target_conf"] > 0 else "/"
        fee = data["sat_per_byte"] if data["sat_per_byte"] > 0 else "/"
        text = "<b>Review transaction</b>" + lb_symbol \
            + "To: " + data["address"] + lb_symbol \
            + "Amount: {0}" + lb_symbol \
            + "Target Conf: " + str(target_conf) + lb_symbol \
            + "Fees(sat/byte): " + str(fee)

        args = [data["amount"]]

        unit = self.userdata.get_selected_unit(username)
        for idx, arg in enumerate(args):
            args[idx] = formatAmount(arg, unit)

        text = text.format(*args)
        return text

    def formatChannelCloseOutput(self, data, closing_data, username, lb_symbol="\n"):
        active_text = "" if data["active"] else "  🔴 <i>offline</i>"
        target_conf = closing_data["target_conf"] if closing_data["target_conf"] > 0 else "/"
        fee = closing_data["sat_per_byte"] if closing_data["sat_per_byte"] > 0 else "/"

        text = "<b>" + data["alias"] + "</b>" + active_text + lb_symbol \
               + data["remote_pubkey"] + lb_symbol \
               + "Capacity: {0}" + lb_symbol \
               + "Local Balance: {1}" + lb_symbol \
               + "Remote Balance: {2}" + lb_symbol + lb_symbol \
               + "Commitment transaction: " + lb_symbol \
               + "Target Conf: " + str(target_conf) + lb_symbol \
               + "Fees(sat/byte): " + str(fee)

        args = [
            int(data["local_balance"]) + int(data["remote_balance"]), int(data["local_balance"]),
            int(data["remote_balance"])
        ]

        unit = self.userdata.get_selected_unit(username)
        for idx, arg in enumerate(args):
            args[idx] = formatAmount(arg, unit)

        text = text.format(*args)
        return text
