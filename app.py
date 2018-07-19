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

def safeSendMessage(id0, status):
    if id0 in connections:
        connections[id0].send(buildMessage(status))

@websocket.route('/socket')
def socket(ws):
    while True:
        msg = ws.receive()
        #ws.send(msg)
        print(msg, type(msg), msg == None, msg == '', msg == b'')
        if msg != None and msg != '' and msg != b'' :
            try:
                jsonDecoded = json.loads(msg)
                print(jsonDecoded, 'request' in jsonDecoded, 'friendCode' in jsonDecoded, 'friendcode' in jsonDecoded, 'part1' in jsonDecoded, 'id0' in jsonDecoded)
                if 'id0' in jsonDecoded and jsonDecoded['id0'] is not None and len(jsonDecoded['id0']) == 32:
                    connections[jsonDecoded['id0']] = ws
                    if 'request' in jsonDecoded and jsonDecoded['request'] == 'bruteforce':
                        db.devices.update_one({'_id': jsonDecoded['id0'], 'lfcs': {'$exists': True}}, {'$set': {'wantsbf': True, 'expirytime': emptytime}}, upsert=True)
                        ws.send(buildMessage('queue'))
                    elif 'friendCode' in jsonDecoded:
                        fc = int(jsonDecoded['friendCode'])
                        if verify_fc(fc):
                            db.devices.update_one({'_id': jsonDecoded['id0']}, {'$set': {'friendcode': fc}}, upsert=True)
                            ws.send(buildMessage('friendCodeProcessing'))
                        else:
                            ws.send(buildMessage('friendCodeAdded'))
                    elif 'part1' in jsonDecoded:
                        db.devices.update_one({'_id': jsonDecoded['id0']}, {'$set': {'wantsbf': True, 'expirytime': datetime.datetime.now() + datetime.timedelta(hours=1), 'lfcs': binascii.a2b_base64(jsonDecoded['lfcs'])}}, upsert=True)
                        ws.send(buildMessage('queue'))
                    else:
                        device = db.devices.find_one({"_id": jsonDecoded['id0']})
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
    try:
        users = db.devices.find_one({"hasadded": {"$ne": True}, "friendcode": {"$exists": True}})
        if users is not None:
            for user in users:
                try:
                    print(user)
                    string += str(user['friendcode'])
                    string += '\n'
                except Exception as e:
                    print("error", e)
        else:
            return 'nothing'
    except:
        return 'nothing'
    if string != '':
        return string
    return 'nothing'

@app.route('/added/<int:fc>')
def added(fc):
    try:
        db.devices.update_one({'friendcode':fc}, {'$set': {'hasadded': True}})
        try:
            thing = db.devices.find_one({'friendcode':fc})
            safeSendMessage(thing['_id'], 'friendCodeAdded')
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
                safeSendMessage(thing['_id'], 'movablePart1')
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
        device = db.devices.find_one({"_id": id0})
        if 'lfcs' in device:
            st = struct.pack('>8s8x', device['lfcs'][::-1])
            print(st)
            st += bytearray(id0, 'ascii')
            st+= bytearray(976)
            resp = make_response(st)
            resp.headers['Content-Type'] = 'application/octet-stream'
            resp.headers['Content-Disposition'] = 'inline; filename="movable_part1.sed"'
            return resp
        else:
            return 'error'
    else:
        return 'error'

@app.route('/movable/<id0>')
def movable(id0):
    if id0 != '':
        device = db.devices.find_one({"_id": id0})
        if 'movable' in device:
            resp = make_response(device['movable'])
            resp.headers['Content-Type'] = 'application/octet-stream'
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
    devicetomine = db.devices.find_one({"hasmovable": {"$ne": True}, "expirytime": {"$ne": emptytime}, "expired": {"$ne": True}, "wantsbf": True, "miner": {"$exists": False}, "cancelled": {"$ne": True}})
    print("thing", devicetomine)
    if devicetomine is not None and '_id' in devicetomine:
        return devicetomine['_id']
    else:
        return 'nothing'

@app.route('/claim/<id0>')
def claim(id0):
    devicetomine = db.devices.find_one({"_id": id0, "hasmovable": {"$ne": True}, "expirytime": {"$ne": emptytime}, "expired": {"$ne": True}, "wantsbf": True, "miner": {"$exists": False}, "cancelled": {"$ne": True}})
    if devicetomine != None:
        db.devices.update_one({"_id": id0}, {'$set': {'miner': request.headers['X-Forwarded-For'], 'expirytime': datetime.datetime.now() + datetime.timedelta(hours=1)}})
        safeSendMessage(id0, 'bruteforcing')
        return 'ok'
    else:
        return 'error'

@app.route('/check/<id0>')
def check(id0):
    db.devices.update({'_id': id0}, {'checktime': datetime.datetime.now()})
    devicetomine = db.devices.find_one({"_id": id0, "hasmovable": {"$ne": True}, "expirytime": {"$gt": datetime.datetime.now()}, "expired": {"$ne": True}, "wantsbf": True, "cancelled": {"$ne": True}})
    if devicetomine != None:
        return 'error'
    else:
        return 'ok'

@app.route('/cancel/<id0>')
def cancel(id0):
    kill = request.args.get('kill', 'n')
    devicetomine = db.devices.find_one({"_id": id0, "hasmovable": {"$ne": True}, "expirytime": {"$gt": datetime.datetime.now()}, "expired": {"$ne": True}, "wantsbf": True, "cancelled": {"$ne": True}, "miner": {"$exists": False}})
    if devicetomine != None:
        db.devices.update_one({"_id": id0}, {'$set': {'miner': request.headers['X-Forwarded-For'], 'cancelled': (kill == 'y'), 'expirytime': datetime.datetime.now() + datetime.timedelta(hours=1)}})
        if kill == 'y':
            safeSendMessage(id0, 'flag')
        else:
            safeSendMessage(id0, 'queue')
        return 'ok'
    else:
        return 'error'

@app.route('/upload/<id0>')
def upload(id0):
    if 'movable' in request.files:
        file = request.files['movable']
        if file.size == 320:
            buf = file.stream.read()
            db.devices.update_one({"_id": id0}, {'$set': {'movable': buf, 'hasmovable': True, 'wantsbf': False }})
            # TODO: leaderboard return
            safeSendMessage(id0, 'done')
            return 'ok'
        else:
            return 'error'
    else:
        return 'error'

# note to anyone trying to run this: static files including index are served through nginx

if __name__ == '__main__':
    app.run(port=8080, gevent=100)
