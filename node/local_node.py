import rpc_pb2 as ln
import rpc_pb2_grpc as lnrpc
import grpc
from google.protobuf.json_format import MessageToDict
import os
from os.path import join, expandvars, expanduser
import codecs
from sys import platform
from helper import logToFile
from time import sleep
import telegram


class LocalNode:

    root_path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, ln_host="127.0.0.1", ln_port=10009, ln_dir="", net="mainnet", ln_cert_path="", ln_admin_macaroon_path=""):
        # init ln
        self.ln_host = ln_host
        self.ln_port = ln_port
        self.net = net
        self.ln_dir = ln_dir
        self.ln_cert_path = ln_cert_path
        self.ln_admin_macaroon_path = ln_admin_macaroon_path

        channel = self.init_ln_connection()
        self.stub = lnrpc.LightningStub(channel)

        self.nodeOnline = False
        self.check_node_online()
        self.sub_sleep_retry = 60
        self.sub_sleep_offline = 30

    def init_ln_connection(self):
        os.environ["GRPC_SSL_CIPHER_SUITES"] = 'HIGH+ECDSA'

        if self.ln_cert_path != "" and self.ln_admin_macaroon_path != "":
            lnd_cert_path = self.ln_cert_path
            lnd_admin_macaroon_path = self.ln_admin_macaroon_path
        else:
            if self.ln_dir != "":
                lnd_root_dir = self.ln_dir  # custom location
            else:
                # default locations
                if platform.startswith("win32") or platform.startswith("cygwin"):  # windows
                    lnd_root_dir = join(expandvars("%LOCALAPPDATA%"), "Lnd")
                elif platform.startswith("linux"):  # linux
                    lnd_root_dir = join(expanduser("~"), ".lnd")
                elif platform.startswith("darwin"):  # Mac OS X
                    lnd_root_dir = join(expanduser("~"), "Library", "Application Support", "Lnd")
                else:
                    lnd_root_dir = join(self.root_path, "..", "lnd")

            lnd_cert_path = join(lnd_root_dir, "tls.cert")
            lnd_admin_macaroon_path = join(lnd_root_dir, "data", "chain", "bitcoin", self.net, "admin.macaroon")

        cert = open(lnd_cert_path, 'rb').read()
        with open(lnd_admin_macaroon_path, 'rb') as f:
            macaroon_bytes = f.read()
            macaroon = codecs.encode(macaroon_bytes, 'hex')

        # build ssl credentials using the cert
        cert_creds = grpc.ssl_channel_credentials(cert)
        # build meta data credentials
        auth_creds = grpc.metadata_call_credentials(lambda context, callback: callback([('macaroon', macaroon)], None))
        # combine the cert credentials and the macaroon auth credentials
        combined_creds = grpc.composite_channel_credentials(cert_creds, auth_creds)

        return grpc.secure_channel(str(self.ln_host) + ":" + str(self.ln_port), combined_creds)

    def check_node_online(self, wait_on_not_synced=60):
        try:
            first = True
            while True:
                lninfo, err_ln_getinfo = self.get_ln_info()

                if err_ln_getinfo is not None:
                    self.nodeOnline = False
                    return {"online": False, "msg": err_ln_getinfo}
                else:
                    if first and lninfo["synced_to_chain"] is False:
                        first = False
                        # wait and check again, to prevent false positives on slow hardware
                        # lightning node 1 block behind, when checking
                        sleep(wait_on_not_synced)
                        continue
                    self.nodeOnline = True
                    return {
                        "online": True,
                        "block_height": lninfo["block_height"],
                        "synced": lninfo["synced_to_chain"],
                        "version": lninfo["version"]
                    }
        except Exception as e:
            text = str(e)
            logToFile("Exception check_node_online: " + text)
            self.nodeOnline = False
            return {"online": None, "msg": "Exception: " + text}

    def subscribe_node_watcher(self, bot, userdata, time_delta=10*60):
        try:
            while True:
                response = self.check_node_online()
                text = ""
                if response["online"] is False:
                    text = "Lightning node is offline!"
                elif response["online"] is None:
                    text = "Cannot check lightning node status, there was error (check logs)."
                else:
                    if response["synced"] is False:
                        text = "Lightning node not synced!\nNode height: "+str(response["block_height"])

                if text != "":
                    for username in userdata.get_usernames():
                        if userdata.get_chat_id(username) is not None and userdata.get_node_watch_mute(username) is False:
                            chat_id = userdata.get_chat_id(username)
                            bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)
                sleep(time_delta)

        except Exception as e:
            text = "Exception LiveFeed LocalNode subscribe node watcher: " + str(e)
            logToFile(text)
            print(text)

    def decode_ln_invoice(self, pay_req):
        try:
            request = ln.PayReqString(pay_req=pay_req)
            response = self.stub.DecodePayReq(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception decode_ln_invoice: " + text)
            return None, text

    def get_ln_node_info(self, pub_key):
        try:
            request = ln.NodeInfoRequest(pub_key=pub_key)
            response = self.stub.GetNodeInfo(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception get_ln_node_info: " + text)
            return None, text

    def get_ln_info(self):
        try:
            response = self.stub.GetInfo(ln.GetInfoRequest())
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception get_ln_info: " + text)
            return None, text

    def get_ln_onchain_address(self, addr_type="p2wkh"):
        try:
            if addr_type == "p2wkh":
                addr_type = "WITNESS_PUBKEY_HASH"
            else:
                addr_type = "NESTED_PUBKEY_HASH"
            request = ln.NewAddressRequest(type=addr_type)
            response = self.stub.NewAddress(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception get_ln_onchain_address: " + text)
            return None, text

    def pay_ln_invoice(self, pay_req):
        try:
            request = ln.SendRequest(payment_request=pay_req)
            response = self.stub.SendPaymentSync(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception pay_ln_invoice: " + text)
            return None, text

    def add_ln_invoice(self, value_sats, memo, expiry_sec):
        try:
            request = ln.Invoice(memo=memo, value=int(value_sats), expiry=int(expiry_sec))
            response = self.stub.AddInvoice(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception add_ln_invoice: " + text)
            return None, text

    def get_channel_list(self):
        try:
            channels = self.stub.ListChannels(ln.ListChannelsRequest())
            return MessageToDict(channels, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception get_channel_list: " + text)
            return None, text

    def connect_peer(self, pubkey, host):
        try:
            addr = ln.LightningAddress(pubkey=pubkey, host=host)
            request = ln.ConnectPeerRequest(addr=addr)
            response = self.stub.ConnectPeer(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception connect_peer: " + text)
            return None, text

    def open_channel(self, node_pubkey, local_funding_amount, private, min_htlc_msat, remote_csv_delay, sat_per_byte, target_conf):
        try:
            args = {"node_pubkey_string": node_pubkey, "local_funding_amount": local_funding_amount, "private": private, "min_htlc_msat": min_htlc_msat}
            if sat_per_byte > 0:
                args["sat_per_byte"] = sat_per_byte
            elif target_conf > 0:
                args["target_conf"] = target_conf

            if remote_csv_delay > 0:
                args["remote_csv_delay"] = remote_csv_delay

            request = ln.OpenChannelRequest(**args)
            response = self.stub.OpenChannelSync(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception open_channel: " + text)
            return None, text

    def get_balance_report(self):
        try:
            onchain_wallet = self.stub.WalletBalance(ln.WalletBalanceRequest())
            channels = self.stub.ListChannels(ln.ListChannelsRequest())

            response = {
                "onchain": MessageToDict(onchain_wallet, including_default_value_fields=True),
                "ln": MessageToDict(channels, including_default_value_fields=True)
            }
            return response, None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception get_balance_report: " + text)
            return None, text

    def subscribe_invoices(self, bot, userdata):

        while True:
            if not self.nodeOnline:
                sleep(self.sub_sleep_offline)  # if we know node is offline, sleep and retry
                continue

            try:
                request = ln.InvoiceSubscription()
                for response in self.stub.SubscribeInvoices(request):
                    json_out = MessageToDict(response, including_default_value_fields=True)
                    # received payment
                    if "settled" in json_out and json_out["settled"] is True:
                        text = "<b>Received LN payment.</b>\n"
                        if "amt_paid_sat" in json_out:
                            text += "Amount: " + "{:,}".format(int(json_out["amt_paid_sat"])).replace(',', '.') + " sats\n"
                        if "memo" in json_out and json_out["memo"] != "":
                            text += "Description: " + json_out["memo"]
                        # send to each user that have chat_id in userdata
                        for username in userdata.get_usernames():
                            if userdata.get_chat_id(username) is not None:
                                chat_id = userdata.get_chat_id(username)
                                bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

            except Exception as e:
                print("LiveFeed LocalNode subscribe invoices: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds")
                sleep(self.sub_sleep_retry)

    def subscribe_channel_events(self, bot, userdata):

        while True:
            if not self.nodeOnline:
                sleep(self.sub_sleep_offline)  # if we know node is offline, sleep and retry
                continue

            try:
                request = ln.ChannelEventSubscription()
                for response in self.stub.SubscribeChannelEvents(request):
                    json_out = MessageToDict(response, including_default_value_fields=True)
                    text = ""
                    if "type" in json_out and json_out["type"] == "OPEN_CHANNEL":
                        channel_data = json_out["open_channel"]
                        text = "<b>New channel opened</b>\n"
                        info_data, error_info = self.get_ln_node_info(pub_key=channel_data["remote_pubkey"])
                        if info_data is None:
                            text += channel_data["remote_pubkey"] + "\n"
                        else:
                            text += info_data["node"]["alias"] + "\n"
                        text += "Capacity: " + "{:,}".format(int(channel_data["capacity"])).replace(',', '.') + " sats\n"
                        text += "Local Balance: " + "{:,}".format(int(channel_data["local_balance"])).replace(',', '.') + " sats\n"
                        text += "Remote Balance: " + "{:,}".format(int(channel_data["remote_balance"])).replace(',', '.') + " sats\n"
                        text += "Time Lock: " + str(channel_data["csv_delay"]) + "\n"
                        private = "yes" if channel_data["private"] else "no"
                        text += "Private: " + private

                    elif "type" in json_out and json_out["type"] == "CLOSED_CHANNEL":
                        channel_data = json_out["closed_channel"]
                        text = "<b>Channel closed</b>\n"
                        info_data, error_info = self.get_ln_node_info(pub_key=channel_data["remote_pubkey"])
                        if info_data is None:
                            text += channel_data["remote_pubkey"] + "\n"
                        else:
                            text += info_data["node"]["alias"] + "\n"
                        text += "Capacity: " + "{:,}".format(int(channel_data["capacity"])).replace(',', '.') + " sats\n"
                        text += "Txid: <a href='{0}" + channel_data["closing_tx_hash"] + "'>"+channel_data["closing_tx_hash"][:8]+"..."+channel_data["closing_tx_hash"][-8:]+"</a>\n"
                        text += "Closure Type: " + str(channel_data["close_type"])

                    if text != "":
                        for username in userdata.get_usernames():
                            if userdata.get_chat_id(username) is not None:
                                chat_id = userdata.get_chat_id(username)
                                text = text.format(userdata.get_default_explorer(username))
                                bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

            except Exception as e:
                print("LiveFeed LocalNode subscribe channel events: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds")
                sleep(self.sub_sleep_retry)

    def subscribe_transactions(self, bot, userdata):

        while True:
            if not self.nodeOnline:
                sleep(self.sub_sleep_offline)  # if we know node is offline, sleep and retry
                continue

            try:
                cache_tx = {}
                request = ln.GetTransactionsRequest()
                for response in self.stub.SubscribeTransactions(request):
                    json_out = MessageToDict(response, including_default_value_fields=True)
                    amount = int(json_out["amount"])
                    if amount > 0:
                        if json_out["num_confirmations"] == 0 and json_out["tx_hash"] not in cache_tx:
                            cache_tx[json_out["tx_hash"]] = json_out["num_confirmations"]
                            text = "<b>Unconfirmed incoming transaction</b>\n"
                        elif json_out["num_confirmations"] >= 1 and json_out["tx_hash"] not in cache_tx:
                            cache_tx[json_out["tx_hash"]] = json_out["num_confirmations"]
                            text = "<b>Received funds confirmed</b>\n"
                        else:
                            cache_tx.pop(json_out["tx_hash"], None)
                            continue  # ignore duplicate
                    elif amount < 0 and json_out["num_confirmations"] >= 1:
                        if json_out["tx_hash"] in cache_tx:
                            cache_tx.pop(json_out["tx_hash"], None)
                            continue  # ignore duplicate
                        else:
                            cache_tx[json_out["tx_hash"]] = json_out["num_confirmations"]
                        text = "<b>Sent transaction confirmed</b>\n"
                        amount = abs(amount)
                    else:
                        continue

                    text += "Amount: " + str(("%.8f" % (amount/100000000)).rstrip('0')) + " BTC\n"
                    total_fees = int(json_out["total_fees"])
                    if total_fees > 0:
                        text += "Fees: " + str(("%.8f" % (total_fees/100000000)).rstrip('0')) + " BTC\n"
                    conf = json_out["num_confirmations"]
                    if conf > 0:
                        text += "Confirmations: " + str(conf) + "\n"
                    text += "Txid: <a href='{0}" + json_out["tx_hash"] + "'>" + json_out["tx_hash"][:8] + "..."+ json_out["tx_hash"][-8:] +"</a>\n"

                    # send to each user that have chat_id in userdata
                    for username in userdata.get_usernames():
                        if userdata.get_chat_id(username) is not None:
                            chat_id = userdata.get_chat_id(username)
                            text = text.format(userdata.get_default_explorer(username))
                            bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

            except Exception as e:
                print("LiveFeed LocalNode subscribe transactions: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds")
                sleep(self.sub_sleep_retry)
