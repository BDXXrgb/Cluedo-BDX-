import random
import string
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'cluedo_secret_key_123'
socketio = SocketIO(app, cors_allowed_origins="*")

SUSPECTS = ["Mlle Rose", "Colonel Moutarde", "Mme Pervenche", "Docteur Olive", "Mme Leblanc", "Professeur Violet"]
ARMES = ["Chandelier", "Couteau", "Revolver", "Corde", "Matraque", "Clé Anglaise"]
LIEUX = ["Salon", "Véranda", "Salle de Bal", "Salle à Manger", "Cuisine", "Bibliothèque", "Billard", "Bureau", "Hall"]

GAMES = {}

def generate_room_code():
    while True:
        code = ''.join(random.choices(string.ascii_uppercase, k=4))
        if code not in GAMES:
            return code

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('create_game')
def handle_create(data):
    username = data.get('username', 'Hôte').strip()
    if not username:
        emit('error', {'msg': 'Choisis un pseudo !'})
        return
        
    room = generate_room_code()
    sid = request.sid
    
    GAMES[room] = {
        "players": {
            sid: {"name": username, "cards": [], "eliminated": False, "position": "Hall"}
        },
        "player_order": [],
        "turn_index": 0,
        "solution": {},
        "started": False
    }
    
    join_room(room)
    emit('room_created', {'room': room, 'players': [username]})

@socketio.on('join_game')
def handle_join(data):
    username = data.get('username', 'Enquêteur').strip()
    room = data.get('room', '').upper().strip()
    sid = request.sid
    
    if not username:
        emit('error', {'msg': 'Choisis un pseudo !'})
        return
    if room not in GAMES:
        emit('error', {'msg': 'Ce code de partie n\'existe pas !'})
        return
    if GAMES[room]["started"]:
        emit('error', {'msg': 'La partie a déjà commencé !'})
        return
        
    GAMES[room]["players"][sid] = {"name": username, "cards": [], "eliminated": False, "position": "Hall"}
    join_room(room)
    
    player_names = [p["name"] for p in GAMES[room]["players"].values()]
    emit('room_update', {'players': player_names, 'room': room}, room=room)

@socketio.on('start_game')
def handle_start(data):
    room = data.get('room', '').upper().strip()
    if room not in GAMES:
        return
    
    game = GAMES[room]
    if len(game["players"]) < 2:
        emit('error', {'msg': 'Il faut au moins 2 joueurs pour lancer !'})
        return
        
    sus_sol = random.choice(SUSPECTS)
    arm_sol = random.choice(ARMES)
    lie_sol = random.choice(LIEUX)
    game["solution"] = {"suspect": sus_sol, "arme": arm_sol, "lieu": lie_sol}
    
    rest_cards = (
        [s for s in SUSPECTS if s != sus_sol] +
        [a for a in ARMES if a != arm_sol] +
        [l for l in LIEUX if l != lie_sol]
    )
    random.shuffle(rest_cards)
    
    sids = list(game["players"].keys())
    for sid in sids:
        game["players"][sid]["cards"] = []
        game["players"][sid]["eliminated"] = False
        game["players"][sid]["position"] = "Hall"
        
    idx = 0
    while rest_cards:
        sid = sids[idx % len(sids)]
        game["players"][sid]["cards"].append(rest_cards.pop())
        idx += 1

    game["player_order"] = sids
    game["turn_index"] = 0
    game["started"] = True
    
    for sid, p_data in game["players"].items():
        emit('game_started', {'cards': p_data["cards"], 'name': p_data["name"]}, room=sid)
        
    send_turn_update(room)

def send_turn_update(room):
    game = GAMES[room]
    current_sid = game["player_order"][game["turn_index"]]
    current_player = game["players"][current_sid]
    
    if current_player["eliminated"]:
        next_turn(room)
        return

    socketio.emit('turn_update', {'current_player': current_player["name"], 'is_your_turn': False}, room=room)
    socketio.emit('turn_update', {'current_player': current_player["name"], 'is_your_turn': True}, room=current_sid)

def next_turn(room):
    game = GAMES[room]
    actives = [s for s in game["player_order"] if not game["players"][s]["eliminated"]]
    if not actives:
        sol = game["solution"]
        socketio.emit('game_over', {'msg': f"💀 Fin de la partie ! Personne n'a trouvé. La solution était : {sol['suspect']} avec le {sol['arme']} au {sol['lieu']}."}, room=room)
        del GAMES[room]
        return
    game["turn_index"] = (game["turn_index"] + 1) % len(game["player_order"])
    send_turn_update(room)

@socketio.on('lancer_des')
def handle_lancer_des(data):
    room = data.get('room', '').upper().strip()
    if room not in GAMES: return
    
    sid = request.sid
    de1 = random.randint(1, 6)
    de2 = random.randint(1, 6)
    total = de1 + de2
    
    p_name = GAMES[room]["players"][sid]["name"]
    socketio.emit('log', {'msg': f"🎲 <b>{p_name}</b> a fait un score de <b>{total}</b> ({de1} + {de2})."}, room=room)
    emit('des_resultat', {'total': total}, room=sid)

@socketio.on('player_move')
def handle_move(data):
    room = data.get('room', '').upper().strip()
    piece = data.get('piece')
    sid = request.sid
    if room not in GAMES: return
    
    GAMES[room]["players"][sid]["position"] = piece
    socketio.emit('pion_update', {'sid': sid, 'name': GAMES[room]["players"][sid]["name"], 'piece': piece}, room=room)

@socketio.on('action_hypothese')
def handle_hypothese(data):
    room = data.get('room', '').upper().strip()
    if room not in GAMES: return
    
    asker_sid = request.sid
    game = GAMES[room]
    asker_name = game["players"][asker_sid]["name"]
    suspect, arme, lieu = data['suspect'], data['arme'], data['lieu']
    
    socketio.emit('log', {'msg': f"🔍 <b>{asker_name}</b> suppose : <i>{suspect} | {arme} | {lieu}</i>"}, room=room)
    
    current_idx = game["player_order"].index(asker_sid)
    for i in range(1, len(game["player_order"])):
        check_idx = (current_idx + i) % len(game["player_order"])
        check_sid = game["player_order"][check_idx]
        check_player = game["players"][check_sid]
        
        matching = [c for c in check_player["cards"] if c in [suspect, arme, lieu]]
        if matching:
            revealed_card = random.choice(matching)
            emit('hypothese_result', {'msg': f"💡 {check_player['name']} vous montre : <b>{revealed_card}</b>"}, room=asker_sid)
            socketio.emit('log', {'msg': f"📋 {check_player['name']} a montré une carte à {asker_name}."}, room=room)
            next_turn(room)
            return
            
    emit('hypothese_result', {'msg': "❌ Personne n'a pu contredire l'hypothèse."}, room=asker_sid)
    socketio.emit('log', {'msg': f"❓ Personne n'a contredit {asker_name}."}, room=room)
    next_turn(room)

@socketio.on('action_accusation')
def handle_accusation(data):
    room = data.get('room', '').upper().strip()
    if room not in GAMES: return
    
    sid = request.sid
    game = GAMES[room]
    p_name = game["players"][sid]["name"]
    suspect, arme, lieu = data['suspect'], data['arme'], data['lieu']
    sol = game["solution"]
    
    if suspect == sol["suspect"] and arme == sol["arme"] and lieu == sol["lieu"]:
        socketio.emit('game_over', {'msg': f"🏆 🎉 Victoire ! <b>{p_name}</b> a résolu le crime ! C'était bien <b>{suspect}</b> avec le/la <b>{arme}</b> dans le/la <b>{lieu}</b> !"}, room=room)
        del GAMES[room]
    else:
        game["players"][sid]["eliminated"] = True
        socketio.emit('log', {'msg': f"❌ <b>{p_name}</b> s'est trompé d'accusation ultime et est éliminé !"}, room=room)
        emit('player_eliminated', room=sid)
        next_turn(room)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)