import rpc_pb2 as ln
import rpc_pb2_grpc as lnrpc
import grpc
from google.protobuf.json_format import MessageToDict
import os
from os.path import join, expandvars, expanduser
import codecs
from sys import platform
from helper import logToFile, formatAmount
from time import sleep
import telegram
from base64 import b64decode
import json
from shutil import copy


class LocalNode:

    root_path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, config, bot, userdata):
        self.bot = bot
        self.userdata = userdata
        # init ln
        self.ln_host = config["lnhost"]
        self.ln_port = config["lnport"]
        self.net = config["lnnet"]
        self.ln_dir = config["lndir"]
        self.ln_cert_path = config["lncertpath"]
        self.ln_admin_macaroon_path = config["lnadminmacaroonpath"]

        self.scb_on_disk = config["scb_on_disk"]
        self.scb_on_disk_path = config["scb_on_disk_path"]

        self.sub_sleep_retry = 60
        self.sub_sleep_offline = 30
        self.node_watcher_sleep = 1*60
        self.not_synced_count = -1

        self.stub = None
        self.nodeOnline = True
        self.nodeOnline_prev = True
        response = self.check_node_online(init=True)
        self.node_status_output(response)

    def init_ln_connection(self):
        try:
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
                    elif platform.startswith("darwin"):  # macOS
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

            channel = grpc.secure_channel(str(self.ln_host) + ":" + str(self.ln_port), combined_creds)
            self.stub = lnrpc.LightningStub(channel)
        except Exception as e:
            logToFile("Exception init_ln_connection: " + str(e))

    def check_node_online(self, init=False, wait_on_not_synced=-1):
        try:
            first = True
            while True:
                if self.nodeOnline is False or init is True:
                    self.init_ln_connection()  # initialize gRPC

                lninfo, err_ln_getinfo = self.get_ln_info(log_enabled=False)
                self.nodeOnline_prev = self.nodeOnline

                if err_ln_getinfo is not None:
                    self.nodeOnline = False
                    self.not_synced_count = -1
                    return {"online": False, "msg": err_ln_getinfo}
                else:
                    if first and lninfo["synced_to_chain"] is False and wait_on_not_synced > 0:
                        first = False
                        # wait and check again, to prevent false positives on slow hardware
                        # lightning node 1 block behind, when checking
                        sleep(wait_on_not_synced)
                        continue
                    self.nodeOnline = True
                    if lninfo["synced_to_chain"] is True:
                        self.not_synced_count = -1
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
            self.not_synced_count = -1
            return {"online": None, "msg": "Exception: " + text}

    def node_status_output(self, response):
        text = ""
        if response["online"] is False and self.nodeOnline_prev is True:
            text = "Lightning node is offline!"
        elif response["online"] is True:
            if response["synced"] is False:
                self.not_synced_count = 0 if self.not_synced_count == 4 else self.not_synced_count + 1
                if self.not_synced_count == 0:
                    text = "Lightning node not synced!\nNode height: " + str(response["block_height"])
            elif self.nodeOnline_prev is False:
                text = "Lightning node is online."

        if text != "":
            for username in self.userdata.get_usernames():
                chat_id = self.userdata.get_chat_id(username)
                if chat_id is not None and self.userdata.get_notifications_state(username)["node"] is True:
                    self.bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

    def subscribe_node_watcher(self):
        try:
            while True:
                response = self.check_node_online(wait_on_not_synced=60)
                self.node_status_output(response)
                sleep(self.node_watcher_sleep)

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

    def get_ln_info(self, log_enabled=True):
        try:
            response = self.stub.GetInfo(ln.GetInfoRequest())
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            if log_enabled:
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

    def send_coins(self, address, amount, sat_per_byte, target_conf, send_all=False):
        try:
            args = {"addr": address, "amount": amount}
            if sat_per_byte > 0:
                args["sat_per_byte"] = sat_per_byte
            elif target_conf > 0:
                args["target_conf"] = target_conf

            if send_all:
                args["send_all"] = True

            request = ln.SendCoinsRequest(**args)
            response = self.stub.SendCoins(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception send_coins: " + text)
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

    def close_channel(self, channel_point, target_conf, sat_per_byte, force):
        try:
            txid = channel_point[:channel_point.find(':')]
            output_index = int(channel_point[channel_point.find(':')+1:])
            funding_txid_bytes = bytes.fromhex(txid)[:: -1]

            args = {"channel_point": {"funding_txid_bytes": funding_txid_bytes, "output_index": output_index}, "force": force}
            if sat_per_byte > 0:
                args["sat_per_byte"] = sat_per_byte
            elif target_conf > 0:
                args["target_conf"] = target_conf

            request = ln.CloseChannelRequest(**args)
            for response in self.stub.CloseChannel(request):
                ret = MessageToDict(response, including_default_value_fields=True)
                return ret, None  # return after close_pending received
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception close_channel: " + text)
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

    def export_all_channel_backups(self):
        try:
            request = ln.ChanBackupExportRequest()
            response = self.stub.ExportAllChannelBackups(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception export_all_channel_backups: " + text)
            return None, text

    def verify_chan_backup(self, single_chan_backups=None, multi_chan_backup=None):
        try:
            if (single_chan_backups is None and multi_chan_backup is None) \
                    or (single_chan_backups is not None and multi_chan_backup is not None):
                return None, "not valid parameters"

            if single_chan_backups is None:
                request = ln.ChanBackupSnapshot(multi_chan_backup=multi_chan_backup)
            else:
                request = ln.ChanBackupSnapshot(single_chan_backups=single_chan_backups)
            response = self.stub.VerifyChanBackup(request)
            return MessageToDict(response, including_default_value_fields=True), None
        except Exception as e:
            if hasattr(e, "_state") and hasattr(e._state, "details"):
                text = str(e._state.details)
            else:
                text = str(e)
            logToFile("Exception verify_chan_backup: " + text)
            return None, text

    def subscribe_invoices(self):

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
                        text += "Amount: {0}\n"
                        if "memo" in json_out and json_out["memo"] != "":
                            text += "Description: " + json_out["memo"]

                        # send to each user that have chat_id in userdata
                        for username in self.userdata.get_usernames():
                            chat_id = self.userdata.get_chat_id(username)
                            if chat_id is not None and self.userdata.get_notifications_state(username)["invoices"] is True:
                                unit = self.userdata.get_selected_unit(username)
                                text = text.format(formatAmount(int(json_out["amt_paid_sat"]), unit))
                                self.bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

            except Exception as e:
                msg = "LiveFeed LocalNode subscribe invoices: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds"
                logToFile(msg)
                sleep(self.sub_sleep_retry)

    def subscribe_channel_events(self):

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
                        total = int(channel_data["local_balance"]) + int(channel_data["remote_balance"])
                        channel_data["local_balance_pct"] = int(round((int(channel_data["local_balance"]) / total) * 100))
                        channel_data["remote_balance_pct"] = int(round((int(channel_data["remote_balance"]) / total) * 100))

                        initiator = "by us can now be used" if channel_data["initiator"] else "by remote peer"
                        text = "<b>New channel opened "+initiator+"</b>\n"
                        node_name = channel_data["remote_pubkey"]

                        info_data, error_info = self.get_ln_node_info(pub_key=channel_data["remote_pubkey"])
                        if info_data is not None and info_data["node"]["alias"] != "":
                            node_name = info_data["node"]["alias"]

                        text += "<a href='{5}" + channel_data["remote_pubkey"] + "'>" + node_name + "</a>\n"
                        text += "Capacity: {0}\n"
                        text += "Local Balance: {1} ("+str(channel_data["local_balance_pct"])+"%)\n"
                        text += "Remote Balance: {2} ("+str(channel_data["remote_balance_pct"])+"%)\n"
                        text += "Time Lock: " + str(channel_data["csv_delay"]) + "\n"
                        private = "yes" if channel_data["private"] else "no"
                        text += "Private: " + private + "\n"
                        fund_txid = channel_data["channel_point"][:channel_data["channel_point"].find(':')]
                        text += "Txid: <a href='{3}" + fund_txid + "'>" + fund_txid[:8] + "..." + fund_txid[-8:] + "</a>\n"

                    elif "type" in json_out and json_out["type"] == "CLOSED_CHANNEL":
                        channel_data = json_out["closed_channel"]
                        text = "<b>Channel closed</b>\n"
                        node_name = channel_data["remote_pubkey"]

                        info_data, error_info = self.get_ln_node_info(pub_key=channel_data["remote_pubkey"])
                        if info_data is not None and info_data["node"]["alias"] != "":
                            node_name = info_data["node"]["alias"]

                        text += "<a href='{5}" + channel_data["remote_pubkey"] + "'>" + node_name + "</a>\n"
                        text += "Capacity: {0}\n"
                        text += "Settled Balance: {4}\n"
                        text += "Txid: <a href='{3}" + channel_data["closing_tx_hash"] + "'>"+channel_data["closing_tx_hash"][:8]+"..."+channel_data["closing_tx_hash"][-8:]+"</a>\n"
                        text += "Closure Type: " + str(channel_data["close_type"]).lower()

                    if text != "":
                        local_balance = int(channel_data["local_balance"]) if "local_balance" in channel_data else 0
                        remote_balance = int(channel_data["remote_balance"]) if "remote_balance" in channel_data else 0
                        settled_balance = int(channel_data["settled_balance"]) if "settled_balance" in channel_data else 0

                        for username in self.userdata.get_usernames():
                            chat_id = self.userdata.get_chat_id(username)
                            if chat_id is not None and self.userdata.get_notifications_state(username)["chevents"] is True:
                                unit = self.userdata.get_selected_unit(username)
                                explorerLink = self.userdata.get_default_explorer(username)
                                searchEngineLink = self.userdata.get_default_node_search_link(username)
                                text = text.format(
                                    formatAmount(int(channel_data["capacity"]), unit),
                                    formatAmount(local_balance, unit),
                                    formatAmount(remote_balance, unit),
                                    explorerLink,
                                    formatAmount(settled_balance, unit),
                                    searchEngineLink
                                )
                                self.bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

            except Exception as e:
                msg = "LiveFeed LocalNode subscribe channel events: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds"
                logToFile(msg)
                sleep(self.sub_sleep_retry)

    def subscribe_transactions(self):

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

                    text += "Amount: {0}\n"
                    total_fees = int(json_out["total_fees"])
                    if total_fees > 0:
                        text += "Fees: {1}\n"
                    conf = json_out["num_confirmations"]
                    if conf > 0:
                        text += "Confirmations: " + str(conf) + "\n"
                    text += "Txid: <a href='{2}" + json_out["tx_hash"] + "'>" + json_out["tx_hash"][:8] + "..."+ json_out["tx_hash"][-8:] +"</a>\n"

                    # send to each user that have chat_id in userdata
                    for username in self.userdata.get_usernames():
                        chat_id = self.userdata.get_chat_id(username)
                        if chat_id is not None and self.userdata.get_notifications_state(username)["transactions"] is True:
                            unit = self.userdata.get_selected_unit(username)
                            explorerLink = self.userdata.get_default_explorer(username)
                            text = text.format(formatAmount(amount, unit), formatAmount(total_fees, unit), explorerLink)
                            self.bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML, disable_web_page_preview=True)

            except Exception as e:
                msg = "LiveFeed LocalNode subscribe transactions: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds"
                logToFile(msg)
                sleep(self.sub_sleep_retry)

    def subscribe_channel_backups(self):

        while True:
            if not self.nodeOnline:
                sleep(self.sub_sleep_offline)
                continue

            try:
                request = ln.ChannelBackupSubscription()
                for response in self.stub.SubscribeChannelBackups(request):
                    json_out = MessageToDict(response, including_default_value_fields=True)
                    multi_ch_bytes = b64decode(json_out["multi_chan_backup"]["multi_chan_backup"])

                    # save to temp file
                    temp_path_local = os.path.join(self.root_path, "..", "temp", "channel.backup")
                    with open(temp_path_local, "wb") as file:
                        file.write(multi_ch_bytes)

                    # read back file and check the integrity of a backup snapshot
                    with open(temp_path_local, "rb") as file:
                        multi_ch_bytes_check = file.read()

                    multi_chan_backup = {
                        "chan_points": json_out["multi_chan_backup"]["chan_points"],
                        "multi_chan_backup": multi_ch_bytes_check
                    }
                    for ch_point in multi_chan_backup["chan_points"]:
                        ch_point["funding_txid_bytes"] = b64decode(ch_point["funding_txid_bytes"])[:: -1]

                    json_out_verify, error = self.verify_chan_backup(multi_chan_backup=multi_chan_backup)
                    msg_verify = "Multi Channel Backup, backup file integrity check failed. "
                    if error is not None:
                        logToFile(msg_verify + str(error))
                        continue
                    if json_out_verify:  # if json_out_verify is not empty dict than verification failed
                        logToFile(msg_verify + json.dumps(json_out_verify))
                        continue

                    # integrity check of backup is successful, we can save file to disk or send in chat
                    try:
                        if self.scb_on_disk:
                            if self.scb_on_disk_path != "" and os.path.exists(self.scb_on_disk_path):
                                copy(temp_path_local, self.scb_on_disk_path)
                            else:
                                copy(temp_path_local, join(self.root_path, "..", "private"))
                    except Exception as e:
                        logToFile("Multi Channel Backup, " + str(e))

                    for username in self.userdata.get_usernames():
                        chat_id = self.userdata.get_chat_id(username)
                        backups_state = self.userdata.get_backups_state(username)
                        if chat_id is not None and backups_state["chatscb"] is True:
                            # delete last backup message
                            if backups_state["last_scb_backup_msg_id"] is not None:
                                try:
                                    self.bot.delete_message(chat_id=chat_id, message_id=backups_state["last_scb_backup_msg_id"])
                                except Exception as e:
                                    logToFile("Multi Channel Backup, last backup message cannot be deleted. (probably more than 48h from last message) user="+str(username))
                            # send new backup file
                            caption_text = "<b>Multi Channel Backup</b>"
                            new_message = self.bot.send_document(chat_id=chat_id, document=open(temp_path_local, "rb"), parse_mode=telegram.ParseMode.HTML,
                                                                    caption=caption_text, filename="channel.backup", disable_notification=True)
                            # save new backup message id in userdata
                            if new_message and hasattr(new_message, "message_id"):
                                self.userdata.set_last_scb_backup_msg_id(username, new_message.message_id)

                    if os.path.exists(temp_path_local):
                        os.remove(temp_path_local)

            except Exception as e:
                msg = "LiveFeed LocalNode subscribe channel backups: connection lost, will retry after " + str(self.sub_sleep_retry) + " seconds"
                logToFile(msg)
                sleep(self.sub_sleep_retry)
