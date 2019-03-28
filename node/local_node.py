import rpc_pb2 as ln
import rpc_pb2_grpc as lnrpc
import grpc
from google.protobuf.json_format import MessageToDict
import os
from os.path import join, expandvars, expanduser
import codecs
from sys import platform
import json
from helper import logToFile
from time import sleep
import telegram
import subprocess


class LocalNode:

    root_path = os.path.dirname(os.path.abspath(__file__))

    def __init__(self, ln_host="127.0.0.1", ln_port=10009, ln_dir="", net="mainnet", ln_cert_path="", ln_admin_macaroon_path=""):
        # init ln
        channel = self.init_ln_connection(ln_host, ln_port, net, ln_dir, ln_cert_path, ln_admin_macaroon_path)
        self.stub = lnrpc.LightningStub(channel)

        self.onlineBitcoin = False
        self.onlineLightning = False
        self.check_node_online()

    def init_ln_connection(self, host, port, net, root_dir, ln_cert_path, ln_admin_macaroon_path):
        os.environ["GRPC_SSL_CIPHER_SUITES"] = 'HIGH+ECDSA'

        if ln_cert_path != "" and ln_admin_macaroon_path != "":
            lnd_cert_path = ln_cert_path
            lnd_admin_macaroon_path = ln_admin_macaroon_path
        else:
            if root_dir != "":
                lnd_root_dir = root_dir  # custom location
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
            lnd_admin_macaroon_path = join(lnd_root_dir, "data", "chain", "bitcoin", net, "admin.macaroon")

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

        return grpc.secure_channel(str(host) + ":" + str(port), combined_creds)

    def check_node_online(self, backend_tolerate_sync_thr=1, ln_tolerate_sync_thr=1):
        try:
            proc = subprocess.Popen("bitcoin-cli getblockchaininfo", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            err_blockchaininfo = response[1].decode("utf-8")
            if err_blockchaininfo == "":
                blockchaininfo = json.loads(response[0].decode("utf-8"))
            if proc.returncode is None:
                proc.kill()

            proc = subprocess.Popen("bitcoin-cli getnetworkinfo", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            response = proc.communicate()
            err_networkinfo = response[1].decode("utf-8")
            if err_networkinfo == "":
                networkinfo = json.loads(response[0].decode("utf-8"))
            if proc.returncode is None:
                proc.kill()

            lninfo, err_ln_getinfo = self.get_ln_info()

            if err_blockchaininfo != "" or err_networkinfo != "":
                err_networkinfo = err_networkinfo.replace('\n', '')
                err_networkinfo = err_networkinfo.replace('\r', '')
                err_blockchaininfo = err_blockchaininfo.replace('\n', '')
                err_blockchaininfo = err_blockchaininfo.replace('\r', '')
                self.onlineBitcoin = False
                self.onlineLightning = False
                return {"bitcoind": {"online": False, "msg": "stderrs: [" + err_blockchaininfo + "] [" + err_networkinfo + "]"}, "ln": {"online": False}}

            elif err_ln_getinfo is not None:
                self.onlineBitcoin = True
                self.onlineLightning = False
                return {
                    "bitcoind": {
                        "online": True,
                        "subversion": networkinfo["subversion"],
                        "protocolversion": networkinfo["protocolversion"],
                        "blocks": blockchaininfo["blocks"],
                        "headers": blockchaininfo["headers"],
                        "synced": True if (blockchaininfo["headers"] - blockchaininfo[
                            "blocks"]) <= backend_tolerate_sync_thr else False
                    },
                    "ln": {"online": False, "msg": "stderrs: [" + err_ln_getinfo + "]"}
                }
            else:
                self.onlineBitcoin = True
                self.onlineLightning = True
                return {
                    "bitcoind": {
                        "online": True,
                        "subversion": networkinfo["subversion"],
                        "protocolversion": networkinfo["protocolversion"],
                        "blocks": blockchaininfo["blocks"],
                        "headers": blockchaininfo["headers"],
                        "synced": True if (blockchaininfo["headers"] - blockchaininfo[
                            "blocks"]) <= backend_tolerate_sync_thr else False
                    },
                    "ln": {
                        "online": True,
                        "block_height": lninfo["block_height"],
                        "synced": True if (blockchaininfo["headers"] - lninfo["block_height"]) <= ln_tolerate_sync_thr else False,
                        "version": lninfo["version"]
                    }
                }

        except Exception as e:
            text = str(e)
            logToFile("Exception check_node_online: " + text)
            self.onlineBitcoin = False
            self.onlineLightning = False
            return {"online": None, "msg": "Exception: " + text}

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

    def subscribe_invoices(self, bot, chat_id):
        sleep_retry = 65
        sleep_offline = 20

        while True:
            if not self.onlineBitcoin or not self.onlineLightning:
                sleep(sleep_offline)  # if we know node is offline, sleep and retry
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
                        bot.send_message(chat_id=chat_id, text=text, parse_mode=telegram.ParseMode.HTML)

            except Exception as e:
                print("LiveFeed LocalNode subscribe invoices: connection lost, will retry after " + str(sleep_retry) + " seconds")
                sleep(sleep_retry)
