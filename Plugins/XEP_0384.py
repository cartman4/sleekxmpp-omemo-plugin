# -*- coding: utf-8 -*-
from sleekxmpp.plugins.base import base_plugin
from omemo.state import OmemoState
from sqlite3 import connect
from sleekxmpp.xmlstream.handler.callback import Callback
from sleekxmpp.xmlstream import ET, tostring
from config import *
from axolotl.state.sessionrecord import SessionRecord
import logging
from sleekxmpp import Message
from Stanzas.OmemoMessage import OmemoMessage
from sleekxmpp.xmlstream import register_stanza_plugin
from sleekxmpp.xmlstream.matcher.xpath import MatchXPath
import base64
from Crypto.Random import random
from Stanzas.helper import omemoMsgDictToStanza, extractDevices
from omemo.state import TRUSTED, UNDECIDED, UNTRUSTED
import binascii


class XEP_0384(base_plugin):
    """
        XEP-0384 Enables Omemo Encryption in SleekXMPP
    """


    def plugin_init(self):
        self.description = "Enables Omemo Multi-Client Encryption"
        self.xep = "0384"
        # We need a Custom Omemo Message Stanza handler
        # Omemo Message are not handled by the sleekxmpp message handler
        self.xmpp.registerHandler(
          Callback('Incoming Omemo Message',
            MatchXPath('{%s}message/{%s}encrypted' % (self.xmpp.default_ns, NS_OMEMO)),
            self.incomingMsg))
        register_stanza_plugin(Message, OmemoMessage)




    def post_init(self):
        base_plugin.post_init(self)



    def getOwnFingerprint(self):
        fpr = binascii.hexlify(
                self.omemo.store.getIdentityKeyPair()
                .getPublicKey().serialize()
            )
        return self.human_hash(fpr[2:])


    def getAllFingerprintsFor(self, jid):
        session_db = self.omemo.store.getSessionsFromJid(jid)
        results = {}
        for item in session_db:
            _id, jid, deviceid, record, active = item
            active = bool(active)
            identity_key = SessionRecord(serialized=record).getSessionState().getRemoteIdentityKey()
            fpr = binascii.hexlify(identity_key.getPublicKey().serialize())
            fpr = self.human_hash(fpr[2:])
            results[deviceid] = {'active': active, 'fingerprint': fpr}
        return results


    def getFingerprintForDevice(self, jid, deviceID):
        session_db = self.omemo.store.getSessionsFromJid(jid)
        for item in session_db:
            _id, jid, deviceid, record, active = item
            if int(deviceID) == int(deviceid):
                active = bool(active)
                identity_key = SessionRecord(serialized=record).getSessionState().getRemoteIdentityKey()
                fpr = binascii.hexlify(identity_key.getPublicKey().serialize())
                fpr = self.human_hash(fpr[2:])
                return fpr, active
                break
        return



    def human_hash(self,fpr):
        fpr = fpr.upper()
        fplen = len(fpr)
        wordsize = fplen // 8
        buf = ''
        for w in range(0, fplen, wordsize):
            buf += '{0} '.format(fpr[w:w + wordsize])
        return buf.rstrip()




    """
        We prepare Stuff for Omemo Support
    """
    def prepareOmemoSupport(self, ownJID):
        # Everyone should know we want to get +notify on NS_DEVICELIST updates
        # Conversations and Gajim-Omemo needs otherwise they think we do not
        # support Omemo
        self.xmpp['xep_0030'].add_feature(NS_NOTIFY)
        # We need a Sqlite3 database for store information
        self.db_connection = connect(str(self.xmpp.ownJID) + "_" + DB_FILE, check_same_thread=False)
        # We init an OmemoState object
        self.omemo = OmemoState(ownJID, self.db_connection)
        # 1. Adding our device id to the list
        self.omemo.add_own_device(self.omemo.own_device_id)
        while True:
            # 2. Publish own device list
            self.publishOwnDeviceList()
            # 3. Fetching our other devices
            self.fetchDeviceList(self.xmpp.ownJID)
            # 4. Check if our deviceid is inside
            if self.omemo.own_device_id in self.omemo.own_devices:
                break
        # Publish our own bundle
        self.publishOwnBundle()


    """ Fetch Bundle information to build session

        Parameters
        -----------
        to      :   String
                    A String of the bare part of the senders JID
        device_id:  String
                    The device id of the senders device

        Return
        ------------
        bundle  :   dict{
                        'signedPreKeyPublic': base64 Decoded Signed Prekey
                        'signedPreKeyId'    : base64 decoded signed prekey id
                        'identityKey'       : base64 decoded identityKey
                        'signedPreKeySignature' : base64 decoded signedPreKeySignature
                        'preKeyId'          : Randomly selected base decoded prekeyid
                        'preKeyPublic'      : Randomly selected prekey decoded
                    }

    """
    def fetchBundleInformation(self, to, device_id):
        # TODO: Check if bundle is complete
        result = self.xmpp['xep_0060'].get_item(to, NS_BUNDLES + str(device_id), None)
        bundle = {}
        for item in result['pubsub']['items']['substanzas']:
            xml_bundle = item['payload']
            for item in xml_bundle:
                if item.tag == "{%s}signedPreKeyPublic" % NS_OMEMO:
                    bundle['signedPreKeyId'] = int(item.attrib['signedPreKeyId'])
                    bundle['signedPreKeyPublic'] = base64.b64decode(item.text)
                elif item.tag == "{%s}signedPreKeySignature" % NS_OMEMO:
                    bundle['signedPreKeySignature'] = base64.b64decode(item.text)
                elif item.tag == "{%s}identityKey" % NS_OMEMO:
                    bundle['identityKey'] = base64.b64decode(item.text)
                elif item.tag == "{%s}prekeys" % NS_OMEMO:
                    prekeys = []
                    for key in item:
                        prekeys.append((int(key.attrib['preKeyId']), key.text))
                    randomPreKey = random.choice(prekeys)
                    bundle['preKeyPublic'] = base64.b64decode(randomPreKey[1])
                    bundle['preKeyId'] = randomPreKey[0]
        return bundle






    """ Publishes our bundle
    """
    def publishOwnBundle(self):
        bundle = self.xmpp['XEP_0384'].omemo.bundle
        str_payload = "<bundle xmlns='%s'>" % NS_OMEMO
        str_payload += "<signedPreKeyPublic signedPreKeyId='%s'>" % bundle['signedPreKeyId']
        str_payload += "%s</signedPreKeyPublic>" % bundle['signedPreKeyPublic']
        str_payload += "<signedPreKeySignature>%s</signedPreKeySignature>" % bundle['signedPreKeySignature']
        str_payload += "<identityKey>%s</identityKey>" % bundle['identityKey']
        str_payload += "<prekeys>"
        for preKey in bundle['prekeys']:
            str_payload += "<preKeyPublic preKeyId='%s'>" % preKey[0]
            str_payload += "%s</preKeyPublic>" % preKey[1]
        str_payload += "</prekeys></bundle>"
        payload = ET.fromstring(str_payload)
        ns_publish = '%s%s' % (NS_BUNDLES, self.xmpp['XEP_0384'].omemo.own_device_id)
        self.xmpp['xep_0060'].publish(None, ns_publish, payload=payload)







    """ Extracting information from the incoming XML Message
        and checks if this message contains our device id
        otherwise ignore

        Parameters
        ----------
        msg :   Message
                A Message Stanza (Omemo encrypted)

        omemo:  OmemoState
                Our current OmemoState instance
    """
    def incomingMsg(self,msg):
        # Get the keys and the reciepients device id
        keys = msg['OmemoMessage'].getKey()
        # Return if message does not contain our device id
        if not self.omemo.own_device_id in keys:
            print "Message does not contain our device key"
        else:
            # We dont need the ressource part anymore
            senderJID = msg['from'].bare
            # Which device has send the message
            senderDevId = int(msg['OmemoMessage'].getSid())
            # We fetch the bundle information from the sender
            sendersBundle = self.fetchBundleInformation(senderJID, str(senderDevId))
            # Build a session if not already happened
            self.omemo.build_session(senderJID, senderDevId, sendersBundle)

            # Create Message Dict needed for decryption
            msg_dict = {
                'sid' : senderDevId,
                'keys': keys,
                'sender_jid' : senderJID,
                'iv'  : msg['OmemoMessage'].getIv(),
                'payload' : base64.b64decode(msg['OmemoMessage']['payload'])
            }

            fpr , active = self.getFingerprintForDevice(senderJID, senderDevId)
            print "==============="
            print "# New Message #"
            print "==============="
            print "Recipient Account: %s" % self.xmpp.ownJID
            print "Sender JID: %s" % senderJID
            print "Sender DeviceID: %s" % senderDevId
            print "Fingerprint: %s" % fpr
            print "Trusted: %s" % self.omemo.isTrusted(senderJID, senderDevId)
            print self.omemo.decrypt_msg(msg_dict)
            # TODO: Make a valid Message Stanza out of the decrypted msg


    """

    """
    def fetchDeviceList(self, targetJID):
        try:
            result = self.xmpp['xep_0060'].get_item(targetJID, NS_DEVICELIST, None)
            # TODO: Check if item exists
            devicelist = extractDevices(result['pubsub']['items']['item']['payload'])
            if targetJID == self.xmpp.ownJID:
                self.omemo.set_own_devices(devicelist)
            else:
                self.omemo.set_devices(targetJID,devicelist)
        except:
            logging.error('Could not retrieve device list from %s' % targetJID)




    """ Preparing and sending the Omemo Message

        Parameters
        ------------
        ownJID      :   string
                        My Jabber ID
        toJID       :   string
                        The Jabber ID of the recipient
        msg         :   string
                        Body of the message

    """
    def sendOmemoMessage(self,ownJID, toJID, msg):
        # We need a Sqlite3 database for store information
        db_connection = connect(str(self.xmpp.ownJID) + "_" + DB_FILE)
        # We init an OmemoState object
        omemo = OmemoState(ownJID, db_connection)
        # Fetch device list
        self.fetchDeviceList(toJID)
        recipientIDs = omemo.device_list_for(toJID)
        # We need the information bundle from every device of the recipient
        # And a session
        for dev in recipientIDs:
            bundle = self.fetchBundleInformation(toJID, str(dev))
            omemo.build_session(toJID, dev, bundle)
            record = omemo.store.loadSession(toJID, dev)
            identity_key = record.getSessionState().getRemoteIdentityKey()
            #print self.omemo.isTrusted(toJID, dev)
            if AUTOTRUST:
                omemo.store.setTrust(identity_key, TRUSTED)
        #Send message to own devices to
        for ownDev in omemo.own_devices:
            bundle = self.fetchBundleInformation(ownJID, ownDev)
            omemo.build_session(ownJID, ownDev, bundle)
            record2 = omemo.store.loadSession(ownJID, ownDev)
            identity_key = record2.getSessionState().getRemoteIdentityKey()
            if AUTOTRUST:
                omemo.store.setTrust(identity_key, TRUSTED)

        msg_dict = omemo.create_msg(ownJID ,toJID, msg)
        omemoMsg = omemoMsgDictToStanza(ownJID, msg_dict)
        self.xmpp.send(omemoMsg)




    """ Publish our own devicelist

    """
    def publishOwnDeviceList(self):
        devices = ""
        for device in self.omemo.own_devices:
            # Add our device IDs to the XML
            devices = devices + ("<device id='%s' />" % device)
        payload = ET.fromstring("<list xmlns='%s'>%s</list>" % (NS_OMEMO,devices))
        self.xmpp['xep_0060'].publish(None, NS_DEVICELIST, payload=payload)
