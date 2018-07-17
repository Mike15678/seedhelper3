from gevent import monkey
monkey.patch_all()

from flask import Flask
from flask import request, make_response
from flask_uwsgi_websocket import WebSocket
from pymongo import MongoClient
import json
import binascii
import datetime
import signal
import hashlib
import struct

def graceful_reload(signum, traceback):
    """Explicitly close some global MongoClient object."""
    client.close()

signal.signal(signal.SIGINT, graceful_reload)

app = Flask(__name__)
websocket = WebSocket(app)
client = MongoClient(connect=False)
db = client.main
connections = {}

#thx to kurisu
def verify_fc(fc):
    fc = int(fc.replace('-', ''))
    if fc > 0x7FFFFFFFFF:
        return None
    principal_id = fc & 0xFFFFFFFF
    checksum = (fc & 0xFF00000000) >> 32
    return (True if hashlib.sha1(struct.pack('<L', principal_id)).digest()[0] >> 1 == checksum else False)

def buildMessage(status):
    message = {'status': status}
    return json.dumps(message)

@websocket.route('/socket')
def socket(ws):
    while True:
        msg = ws.receive()
        #ws.send(msg)
        print(msg, type(msg), msg == None, msg == '', msg == b'')
        if msg != None and msg != '' and msg != b'' :
            try:
                decode = json.loads(msg)
                print(decode)
                if 'id0' in decode and len(decode['id0']) == 32:
                    connections[decode['id0']] = ws
                    if 'friendCode' in decode:
                        fc = int(decode['friendCode'])
                        if verify_fc(fc):
                            db.devices.update({'id0': decode['id0']}, {'friendcode': fc}, upsert=True)
                            connections[decode['id0']].send(buildMessage('friendCodeProcessing'))
                        else:
                            connections[decode['id0']].send(buildMessage('friendCodeAdded'))
                    else:
                        db.devices.find()
                        device = db.devices.find_one({"id0": decode['id0']})
                        if 'lfcs' in device: 
                            connections[decode['id0']].send(buildMessage('movablePart1'))
                        elif 'hasadded' in device and device['hasadded'] == True:
                            connections[decode['id0']].send(buildMessage('friendCodeAdded'))
                        else:
                            connections[decode['id0']].send(buildMessage('friendCodeProcessing'))
            except Exception as e:
                print("socket json decode fail", e)
        else:
            return

@app.route('/getfcs')
def getfcs():
    string = ''
    for user in db.devices.find({"hasadded": {"$ne": True}}):
        try:
            string += str(user['friendcode'])
            string += '\n'
        except Exception as e:
            print("error", e)
    if string != '':
        return string
    return 'nothing'

@app.route('/added/<int:fc>')
def added(fc):
    try:
        db.devices.update({'friendcode':fc}, {'$set': {'hasadded': True}})
        try:
            thing = db.devices.find({'friendcode':fc})
            connections[thing['id0']].send(buildMessage('friendCodeAdded'))
        except:
            return 'error'
        return 'ok'
    except:
        return 'error'
    #

@app.route('/lfcs/<int:fc>')
def lfcs(fc):
    lfcs = binascii.unhexlify(request.args.get('lfcs', None))
    if lfcs != None:
        try:
            db.devices.update({'friendcode':fc}, {'$set': {'lfcs':lfcs}})
            try:
                thing = db.devices.find({'friendcode':fc})
                connections[thing['id0']].send(buildMessage('movablePart1'))
            except:
                return 'error'
            return 'ok'
        except:
            return 'error'
    else:
        return 'error'

@app.route('/part1/<id0>')
def part1(id0):
    if id0 != '':
        device = db.devices.find_one({"id0": id0})
        if 'lfcs' in device: 
            st = struct.pack('<Q8x', device['lfcs'])
            print(st)
            st += bytearray(id0)
            resp = make_response(st)
            resp.headers['Content-Disposition'] = 'inline; filename="movable_part1.sed"'
            return resp
        else:
            return 'error'
    else:
        return 'error'

# note to anyone trying to run this: static files including index are served through nginx

if __name__ == '__main__':
    app.run(port=8080)