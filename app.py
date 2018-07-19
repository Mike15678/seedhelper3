from gevent import monkey
monkey.patch_all()

from flask import Flask
from flask import request, make_response
from flask_uwsgi_websocket import WebSocket, GeventWebSocket
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
websocket = GeventWebSocket(app)
client = MongoClient(connect=False)
db = client.main
connections = {}
# backwards compatible zero time from sh2 go
emptytime = datetime.datetime(1,1,1)

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
                if 'id0' in decode and decode['id0'] is not None and len(decode['id0']) == 32:
                    connections[decode['id0']] = ws
                    if 'request' in decode and decode['request'] == 'bruteforce':
                        db.devices.update_one({'id0': decode['id0'], 'lfcs': {'$exists': True}}, {'$set': {'wantsbf': True, 'expirytime': emptytime}}, upsert=True)
                        ws.send(buildMessage('queue'))
                    elif 'friendCode' in decode:
                        fc = int(decode['friendCode'])
                        if verify_fc(fc):
                            db.devices.update_one({'id0': decode['id0']}, {'$set': {'friendcode': fc}}, upsert=True)
                            ws.send(buildMessage('friendCodeProcessing'))
                        else:
                            ws.send(buildMessage('friendCodeAdded'))
                    elif 'part1' in decode:
                        db.devices.update_one({'id0': decode['id0']}, {'$set': {'wantsbf': True, 'expirytime': datetime.datetime.now() + datetime.timedelta(hours=1), 'lfcs': binascii.a2b_base64(decode['lfcs'])}}, upsert=True)
                        ws.send(buildMessage('queue'))
                    else:
                        device = db.devices.find_one({"id0": decode['id0']})
                        if 'lfcs' in device: 
                            ws.send(buildMessage('movablePart1'))
                        elif 'hasadded' in device and device['hasadded'] == True:
                            ws.send(buildMessage('friendCodeAdded'))
                        else:
                            ws.send(buildMessage('friendCodeProcessing'))
            except Exception as e:
                print("socket json decode fail", e)

@app.route('/getfcs')
def getfcs():
    string = ''
    for user in db.devices.find_one({"hasadded": {"$ne": True}, "friendcode": {"$exists": True}}):
        try:
            print(user)
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
        db.devices.update_one({'friendcode':fc}, {'$set': {'hasadded': True}})
        try:
            thing = db.devices.find_one({'friendcode':fc})
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
            db.devices.update_one({'friendcode':fc}, {'$set': {'lfcs':lfcs}})
            try:
                thing = db.devices.find_one({'friendcode':fc})
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

@app.route('/movable/<id0>')
def movable(id0):
    if id0 != '':
        device = db.devices.find_one({"id0": id0})
        if 'movable' in device:
            resp = make_response(device['movable'])
            resp.headers['Content-Disposition'] = 'inline; filename="movable_part1.sed"'
            return resp
        else:
            return 'error'
    else:
        return 'error'

@app.route('/getwork')
def getwork():
    currentlymining = db.devices.count_documents({"miner": request.headers['X-Forwarded-For'], "hasmovable": {"$ne": True}, "expirytime": {"$ne": emptytime}, "expired": {"$ne": True}})
    if currentlymining > 0:
        return 'nothing'
    devicetomine = db.devices.find_one({"hasmovable": {"$ne": True}, "expirytime": {"$ne": emptytime}, "expired": {"$ne": True}, "wantsbf": True, "cancelled": {"$ne": True}})
    print(devicetomine)
    if devicetomine is not None and 'id0' in devicetomine:
        return devicetomine['id0']
    else:
        return 'nothing'

@app.route('/claim/<id0>')
def claim(id0):
    devicetomine = db.devices.find_one({"id0": id0, "hasmovable": {"$ne": True}, "expirytime": {"$ne": emptytime}, "expired": {"$ne": True}, "wantsbf": True, "miner": {"$exists": False}, "cancelled": {"$ne": True}})
    if devicetomine != None:
        db.devices.update_one({"id0": id0}, {'$set': {'miner': request.headers['X-Forwarded-For'], 'expirytime': datetime.datetime.now() + datetime.timedelta(hours=1)}})
        connections[id0].send(buildMessage('bruteforcing'))
        return 'ok'
    else:
        return 'error'

@app.route('/check/<id0>')
def check(id0):
    devicetomine = db.devices.find_one({"id0": id0, "hasmovable": {"$ne": True}, "expirytime": {"$gt": datetime.datetime.now()}, "expired": {"$ne": True}, "wantsbf": True, "cancelled": {"$ne": True}})
    if devicetomine != None:
        return 'error'
    else:
        return 'ok'

@app.route('/cancel/<id0>')
def cancel(id0):
    kill = request.args.get('kill', 'n')
    devicetomine = db.devices.find_one({"id0": id0, "hasmovable": {"$ne": True}, "expirytime": {"$gt": datetime.datetime.now()}, "expired": {"$ne": True}, "wantsbf": True, "cancelled": {"$ne": True}, "miner": {"$exists": False}})
    if devicetomine != None:
        db.devices.update_one({"id0": id0}, {'$set': {'miner': request.headers['X-Forwarded-For'], 'cancelled': (kill == 'y'), 'expirytime': datetime.datetime.now() + datetime.timedelta(hours=1)}})
        if kill == 'y':
            connections[id0].send(buildMessage('flag'))
        else:
            connections[id0].send(buildMessage('queue'))
        return 'ok'
    else:
        return 'error'

@app.route('/upload/<id0>')
def upload(id0):
    if 'movable' in request.files:
        file = request.files['movable']
        if file.size == 320:
            buf = file.stream.read()
            db.devices.update_one({"id0": id0}, {'$set': {'movable': buf, 'hasmovable': True, 'wantsbf': False }})
            # TODO: leaderboard return
            connections[id0].send(buildMessage('done'))
            return 'ok'
        else:
            return 'error'
    else:
        return 'error'

# note to anyone trying to run this: static files including index are served through nginx

if __name__ == '__main__':
    app.run(port=8080, gevent=100)
