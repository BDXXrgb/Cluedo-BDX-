import random
import time
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_cluedo_key_ultra'
socketio = SocketIO(app, cors_allowed_origins="*")

SUSPECTS = ["Mlle Rose", "Colonel Moutarde", "Mme Pervenche", "Docteur Olive", "Mme Leblanc", "Professeur Violet"]
ARMES = ["Chandelier", "Couteau", "Revolver", "Corde", "Matraque", "Clé Anglaise"]
LIEUX = ["Salon", "Véranda", "Salle de Bal", "Salle à Manger", "Cuisine", "Bibliothèque", "Billard", "Bureau", "Hall"]

salons = {}

@app.route('/')
def index():
    return render_template('index.html')

# --- LOGIQUE DU COMPTE À REBOURS (TIMER) ---
def start_room_timer(room_code):
    if room_code not in salons: return
    game = salons[room_code]
    game['timer_value'] = 60
    game['timer_active'] = True
    
    while game['timer_value'] > 0 and game['timer_active'] and game['started']:
        socketio.emit('timer_update', {'seconds': game['timer_value']}, to=room_code)
        socketio.sleep(1)
        game['timer_value'] -= 1
        
    # Si le temps s'est écoulé sans action, on skip automatiquement
    if game['timer_value'] <= 0 and game['started'] and game['timer_active']:
        socketio.emit('log', {'msg': "⏳ Temps écoulé ! Le tour passe automatiquement."}, to=room_code)
        passer_au_tour_suivant(room_code)

# --- GESTION DES SALONS & LOBBY ---
@socketio.on('create_game')
def handle_create_game(data):
    username = data.get('username', 'Anonyme')
    character = data.get('character', 'Mlle Rose')
    while True:
        room_code = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=4))
        if room_code not in salons: break
            
    salons[room_code] = {
        'players': {
            request.sid: {'name': username, 'char': character, 'cards': [], 'piece': 'Hall', 'eliminated': False}
        },
        'order': [request.sid],
        'turn_idx': 0,
        'solution': {},
        'started': False,
        'forced_dice': None,
        'timer_value': 60,
        'timer_active': False
    }
    
    join_room(room_code)
    emit('room_created', {'room': room_code, 'players': [{'name': username, 'char': character}]})

@socketio.on('join_game')
def handle_join_game(data):
    username = data.get('username', 'Anonyme')
    character = data.get('character', 'Mme Pervenche')
    room_code = data.get('room', '').upper()
    
    if room_code not in salons:
        emit('error', {'msg': "Salon introuvable !"})
        return
    game = salons[room_code]
    if game['started']:
        emit('error', {'msg': "Partie en cours. Utilisez Rejoindre en cours !"})
        return
    if len(game['players']) >= 6:
        emit('error', {'msg': "Salon plein !"})
        return

    game['players'][request.sid] = {'name': username, 'char': character, 'cards': [], 'piece': 'Hall', 'eliminated': False}
    game['order'].append(request.sid)
    join_room(room_code)
    
    players_list = [{'name': p['name'], 'char': p['char']} for p in game['players'].values()]
    emit('room_update', {'room': room_code, 'players': players_list}, to=room_code)

@socketio.on('join_in_game')
def handle_join_in_game(data):
    username = data.get('username', 'Anonyme')
    character = data.get('character', 'Docteur Olive')
    room_code = data.get('room', '').upper()
    
    if room_code not in salons: return
    game = salons[room_code]
    
    game['players'][request.sid] = {'name': username, 'char': character, 'cards': [], 'piece': 'Hall', '
