import random
import time
import json
import os
import re
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_cluedo_key_12345'
socketio = SocketIO(app, cors_allowed_origins="*")

SUSPECTS = ["Mlle Rose", "Colonel Moutarde", "Mme Pervenche", "Docteur Olive", "Mme Leblanc", "Professeur Violet"]
ARMES = ["Chandelier", "Couteau", "Revolver", "Corde", "Matraque", "Clé Anglaise"]
LIEUX = ["Salon", "Véranda", "Salle de Bal", "Salle à Manger", "Cuisine", "Bibliothèque", "Billard", "Bureau", "Hall"]

# 🤬 LISTE DES MOTS BANNIS (Gros mots et insultes)
BAD_WORDS = [
    r"fils de p\w*", r"putain", r"merde", r"connard", r"salope", 
    r"encul\w*", r"fdp", r"ntm", r"chiasse", r"bâtard, r"TDC",, r"trout du cul"
]

salons = {}
LEADERBOARD_FILE = "leaderboard.json"

def load_leaderboard():
    if not os.path.exists(LEADERBOARD_FILE):
        return {}
    try:
        with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_victory(username):
    if not username or username == "Anonyme":
        return
    data = load_leaderboard()
    data[username] = data.get(username, 0) + 1
    try:
        with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print("Erreur écriture classement :", e)

# 🔒 FONCTION DE SÉCURITÉ : Détection et Censure
def check_and_censor(text):
    censored = text
    found_bad = False
    for pattern in BAD_WORDS:
        if re.search(pattern, censored, re.IGNORECASE):
            censored = re.sub(pattern, "####", censored, flags=re.IGNORECASE)
            found_bad = True
    return censored, found_bad

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('get_leaderboard')
def handle_get_leaderboard():
    data = load_leaderboard()
    sorted_top = sorted(data.items(), key=lambda x: x[1], reverse=True)[:5]
    emit('leaderboard_data', {'top': sorted_top})

@socketio.on('create_game')
def handle_create_game(data):
    username = data.get('username', 'Anonyme')
    while True:
        room_code = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=4))
        if room_code not in salons:
            break
            
    salons[room_code] = {
        'players': {request.sid: {'name': username, 'cards': [], 'piece': 'Hall', 'eliminated': False, 'warnings': 0}},
        'order': [request.sid],
        'turn_idx': 0,
        'solution': {},
        'started': False,
        'forced_dice': None,
        'timer_count': 60,
        'remaining_deck': []
    }
    join_room(room_code)
    emit('room_created', {'room': room_code, 'players': [username]})

@socketio.on('join_game')
def handle_join_game(data):
    username = data.get('username', 'Anonyme')
    room_code = data.get('room', '').upper()
    if room_code not in salons:
        emit('error_msg', {'msg': "Ce salon n'existe pas !"})
        return
    game = salons[room_code]
    if game['started']:
        emit('error_msg', {'msg': "La partie a déjà commencé. Utilisez 'Rejoindre en cours'."})
        return
    if len(game['players']) >= 6:
        emit('error_msg', {'msg': "Le salon est plein (max 6 joueurs)."})
        return

    game['players'][request.sid] = {'name': username, 'cards': [], 'piece': 'Hall', 'eliminated': False, 'warnings': 0}
    game['order'].append(request.sid)
    join_room(room_code)
    liste_noms = [p['name'] for p in game['players'].values()]
    emit('room_update', {'room': room_code, 'players': liste_noms}, to=room_code)

@socketio.on('join_in_game')
def handle_join_in_game(data):
    username = data.get('username', 'Anonyme')
    room_code = data.get('room', '').upper()
    if room_code not in salons:
        emit('error_msg', {'msg': "Ce salon n'existe pas !"})
        return
        
    game = salons[room_code]
    if not game['started']:
        emit('error_msg', {'msg': "La partie n'a pas encore commencé. Rejoignez normalement !"})
        return

    game['players'][request.sid] = {'name': username, 'cards': [], 'piece': 'Hall', 'eliminated': False, 'warnings': 0}
    game['order'].append(request.sid)
    join_room(room_code)

    # Rééquilibrage équitable pour le nouveau joueur
    cartes_attribuees = []
    if game['remaining_deck']:
        nb_a_prendre = min(3, len(game['remaining_deck']))
        for _ in range(nb_a_prendre):
            cartes_attribuees.append(game['remaining_deck'].pop(0))
    else:
        # On pioche chez ceux qui en ont le plus pour équilibrer
        for sid, p in game['players'].items():
            if sid != request.sid and len(p['cards']) > 3:
                cartes_attribuees.append(p['cards'].pop())
                if len(cartes_attribuees) >= 3:
                    break

    game['players'][request.sid]['cards'] = cartes_attribuees
    
    emit('game_started', {'cards': cartes_attribuees, 'is_rejoin': True}, to=request.sid)
    emit('log', {'msg': f"⚡ <b>{username}</b> a rejoint l'enquête en cours !", 'type': 'system'}, to=room_code)

    for s_id, p_info in game['players'].items():
        emit('pion_update', {'sid': s_id, 'name': p_info['name'], 'piece': p_info['piece']}, to=room_code)
    envoyer_changement_tour(room_code)

@socketio.on('send_chat_msg')
def handle_chat_msg(data):
    room_code = data.get('room')
    msg = data.get('msg', '').strip()
    if room_code not in salons or not msg:
        return
    
    game = salons[room_code]
    player = game['players'].get(request.sid)
    if not player:
        return
        
    # 🔒 SÉCURITÉ : Bloquer complètement le chat si le joueur est éliminé
    if player['eliminated']:
        emit('error_msg', {'msg': "🔴 Vous êtes éliminé de l'enquête, vous ne pouvez plus envoyer de messages dans le chat !"})
        return
    
    clean_msg, has_insult = check_and_censor(msg)
    
    if has_insult:
        player['warnings'] += 1
        if player['warnings'] >= 2:
            # Élimination directe et mise en sourdine si récidive
            player['eliminated'] = True
            emit('log', {'msg': f"🤬 <b>{player['name']}</b> a envoyé un message inapproprié : <span style='color:red;'>{clean_msg}</span>", 'type': 'chat'}, to=room_code)
            emit('log', {'msg': f"💀 <b>SÉCURITÉ :</b> {player['name']} a été éliminé et muté pour comportement toxique !", 'type': 'elimination'}, to=room_code)
            emit('player_eliminated', to=request.sid)
            if request.sid == game['order'][game['turn_idx']]:
                passer_au_tour_suivant(room_code)
            return
        else:
            # Simple avertissement
            emit('log', {'msg': f"🤬 <b>{player['name']} :</b> {clean_msg}", 'type': 'chat'}, to=room_code)
            emit('log', {'msg': f"⚠️ <b>Avertissement Sécurité</b> pour {player['name']}. Attention à votre langage (1/2) !", 'type': 'admin'}, to=room_code)
            return

    emit('log', {'msg': f"<b>{player['name']} :</b> {clean_msg}", 'type': 'chat'}, to=room_code)

@socketio.on('start_game')
def handle_start_game(data):
    room_code = data.get('room')
    if room_code not in salons:
        return
    game = salons[room_code]
    if len(game['players']) < 2 or game['started']:
        return

    meurtrier, arme, lieu = random.choice(SUSPECTS), random.choice(ARMES), random.choice(LIEUX)
    game['solution'] = {'suspect': meurtrier, 'arme': arme, 'lieu': lieu}
    
    toutes_cartes = SUSPECTS + ARMES + LIEUX
    toutes_cartes.remove(meurtrier)
    toutes_cartes.remove(arme)
    toutes_cartes.remove(lieu)
    random.shuffle(toutes_cartes)
    
    nb_joueurs = len(game['order'])
    cartes_par_joueur = len(toutes_cartes) // nb_joueurs
    
    for idx, sid in enumerate(game['order']):
        game['players'][sid]['cards'] = toutes_cartes[idx*cartes_par_joueur : (idx+1)*cartes_par_joueur]
        
    game['remaining_deck'] = toutes_cartes[nb_joueurs*cartes_par_joueur:]
        
    game['started'] = True
    for sid, p_info in game['players'].items():
        emit('game_started', {'cards': p_info['cards'], 'is_rejoin': False}, to=sid)
        
    emit('log', {'msg': "🚀 <b>L'enquête commence ! Respectez les autres joueurs dans le chat.</b>", 'type': 'system'}, to=room_code)
    for sid, p_info in game['players'].items():
        emit('pion_update', {'sid': sid, 'name': p_info['name'], 'piece': 'Hall'}, to=room_code)
        
    envoyer_changement_tour(room_code)
    socketio.start_background_task(run_room_timer, room_code)

def run_room_timer(room_code):
    while room_code in salons and salons[room_code]['started']:
        socketio.sleep(1)
        if room_code not in salons or not salons[room_code]['started']:
            break
        game = salons[room_code]
        game['timer_count'] -= 1
        emit('timer_tick', {'left': game['timer_count']}, to=room_code)
        if game['timer_count'] <= 0:
            emit('log', {'msg': "⏰ <b>Temps écoulé !</b> Tour suivant.", 'type': 'system'}, to=room_code)
            passer_au_tour_suivant(room_code)

@socketio.on('lancer_des')
def handle_lancer_des(data):
    room_code = data.get('room')
    if room_code not in salons:
        return
    game = salons[room_code]
    if request.sid != game['order'][game['turn_idx']]:
        return
    
    if game.get('forced_dice') is not None:
        total = game['forced_dice']
        game['forced_dice'] = None 
        emit('log', {'msg': f"🎲 <b>{game['players'][request.sid]['name']}</b> lance les dés et fait magiquement un total de <b>{total}</b> !", 'type': 'admin'}, to=room_code)
    else:
        total = random.randint(1, 6) + random.randint(1, 6)
        emit('log', {'msg': f"🎲 <b>{game['players'][request.sid]['name']}</b> a obtenu <b>{total}</b> !", 'type': 'system'}, to=room_code)
        
    emit('des_resultat', {'total': total}, to=room_code)

@socketio.on('player_move')
def handle_player_move(data):
    room_code = data.get('room')
    piece = data.get('piece')
    if room_code not in salons or request.sid not in salons[room_code]['players']:
        return
    salons[room_code]['players'][request.sid]['piece'] = piece
    emit('pion_update', {'sid': request.sid, 'name': salons[room_code]['players'][request.sid]['name'], 'piece': piece}, to=room_code)

@socketio.on('action_hypothese')
def handle_hypothese(data):
    room_code = data.get('room')
    suspect, arme, lieu = data.get('suspect'), data.get('arme'), data.get('lieu')
    if room_code not in salons:
        return
    
    game = salons[room_code]
    demandeur_sid = request.sid
    demandeur_nom = game['players'][demandeur_sid]['name']
    emit('log', {'msg': f"🔍 <b>{demandeur_nom}</b> soupçonne : <i>{suspect} / {arme} / {lieu}</i>.", 'type': 'hypothese'}, to=room_code)
    
    # RÈGLE : Téléportation automatique du suspect désigné dans la pièce
    for sid, p_info in game['players'].items():
        if p_info['name'] == suspect:
            p_info['piece'] = lieu
            emit('pion_update', {'sid': sid, 'name': p_info['name'], 'piece': lieu}, to=room_code)

    idx_demandeur = game['order'].index(demandeur_sid)
    carte_trouvee, joueur_qui_montre = None, None
    for i in range(1, len(game['order'])):
        check_idx = (idx_demandeur + i) % len(game['order'])
        target_sid = game['order'][check_idx]
        matches = [c for c in game['players'][target_sid]['cards'] if c in [suspect, arme, lieu]]
        if matches:
            carte_trouvee = random.choice(matches)
            joueur_qui_montre = game['players'][target_sid]['name']
            break
            
    repondeur_label = "Personne"
    if carte_trouvee:
        repondeur_label = joueur_qui_montre
        emit('log', {'msg': f"🃏 <b>{joueur_qui_montre}</b> a montré un indice secret à <b>{demandeur_nom}</b>.", 'type': 'hypothese'}, to=room_code)
        emit('hypothese_result', {'demandeurs_uniquement': True, 'carte_devoilee': carte_trouvee}, to=demandeur_sid)
    else:
        emit('log', {'msg': "❌ Personne n'a contredit cette piste.", 'type': 'hypothese'}, to=room_code)
        
    emit('notebook_auto_update', {'demandeur': demandeur_nom, 'suspect': suspect, 'arme': arme, 'lieu': lieu, 'repondeur': repondeur_label}, to=room_code)
    passer_au_tour_suivant(room_code)

@socketio.on('action_accusation')
def handle_accusation(data):
    room_code = data.get('room')
    suspect, arme, lieu = data.get('suspect'), data.get('arme'), data.get('lieu')
    if room_code not in salons:
        return
    game = salons[room_code]
    sol = game['solution']
    nom_acc = game['players'][request.sid]['name']
    
    if suspect == sol['suspect'] and arme == sol['arme'] and lieu == sol['lieu']:
        save_victory(nom_acc)
        emit('game_over_event', {'msg': f"🎉 VICTOIRE ! {nom_acc} a démasqué {sol['suspect']} ({sol['arme']} / {sol['lieu']}) !", 'status': 'win'}, to=room_code)
        game['started'] = False
    else:
        # 🔒 Élimination définitive de l'accusation ultime (le joueur est mute)
        emit('log', {'msg': f"💀 <b>Fausse piste !</b> {nom_acc} a perdu, est éliminé et mis en sourdine dans le chat.", 'type': 'elimination'}, to=room_code)
        game['players'][request.sid]['eliminated'] = True
        emit('player_eliminated', to=request.sid)
        
        actifs = [s for s, p in game['players'].items() if not p['eliminated']]
        if not actifs:
            emit('game_over_event', {'msg': f"💀 Fin de partie ! La solution était : {sol['suspect']} ({sol['arme']} / {sol['lieu']})", 'status': 'fail'}, to=room_code)
            game['started'] = False
        else:
            passer_au_tour_suivant(room_code)

def passer_au_tour_suivant(room_code):
    game = salons[room_code]
    if not game['started']:
        return
    while True:
        game['turn_idx'] = (game['turn_idx'] + 1) % len(game['order'])
        if not game['players'][game['order'][game['turn_idx']]]['eliminated']:
            break
    game['timer_count'] = 60
    envoyer_changement_tour(room_code)

def envoyer_changement_tour(room_code):
    game = salons[room_code]
    active_sid = game['order'][game['turn_idx']]
    for sid in game['players'].keys():
        emit('turn_update', {'is_your_turn': (sid == active_sid), 'current_player': game['players'][active_sid]['name']}, to=sid)


# 👑 ==================== ACTIONS DU PANNEAU ADMIN AVANCÉ ==================== 👑

@socketio.on('admin_teleport_player')
def on_admin_teleport(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    piece = data.get('piece')
    if room_code not in salons: 
        return
    
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            p_info['piece'] = piece
            emit('pion_update', {'sid': sid, 'name': target_name, 'piece': piece}, to=room_code)
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Bedy a téléporté <b>{target_name}</b> dans le/la <b>{piece}</b> !", 'type': 'admin'}, to=room_code)
            break

@socketio.on('admin_inspect_cards')
def on_admin_inspect(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    if room_code not in salons: 
        return
    
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            emit('admin_secret_cards', {'target': target_name, 'cards': p_info['cards']}, to=request.sid)
            break

@socketio.on('admin_modify_timer')
def on_admin_timer(data):
    room_code = data.get('room')
    seconds = int(data.get('seconds', 0))
    if room_code in salons and salons[room_code]['started']:
        salons[room_code]['timer_count'] = max(5, salons[room_code]['timer_count'] + seconds)
        emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Le minuteur a été modifié de <b>{seconds}s</b> par Bedy !", 'type': 'admin'}, to=room_code)

@socketio.on('admin_send_private_hint')
def on_admin_hint(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    hint = data.get('hint', '').strip()
    if room_code not in salons: 
        return
    
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            emit('receive_private_hint', {'hint': hint}, to=sid)
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Indice secret envoyé à {target_name}.", 'type': 'admin'}, to=request.sid)
            break

@socketio.on('admin_trigger_screamer')
def on_admin_screamer(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    if room_code not in salons: 
        return
    
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            emit('trigger_screamer_popup', to=sid)
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Screamer envoyé sur l'écran de <b>{target_name}</b> 😈 !", 'type': 'admin'}, to=request.sid)
            break

@socketio.on('admin_kill_player')
def on_admin_kill(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    if room_code not in salons: 
        return
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            p_info['eliminated'] = True
            emit('player_eliminated', to=sid)
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Bedy a éliminé et muté 💀 <b>{target_name}</b> !", 'type': 'admin'}, to=room_code)
            if sid == salons[room_code]['order'][salons[room_code]['turn_idx']]:
                passer_au_tour_suivant(room_code)
            break

@socketio.on('admin_revive_player')
def on_admin_revive(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    if room_code not in salons: 
        return
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            p_info['eliminated'] = False
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Bedy a ressuscité 😇 <b>{target_name}</b> !", 'type': 'admin'}, to=room_code)
            envoyer_changement_tour(room_code)
            break

@socketio.on('admin_force_dice')
def on_admin_force_dice(data):
    room_code = data.get('room')
    val = data.get('value')
    if room_code in salons:
        salons[room_code]['forced_dice'] = val

@socketio.on('admin_reveal_solution')
def on_admin_reveal(data):
    room_code = data.get('room')
    if room_code in salons and salons[room_code]['solution']:
        emit('admin_reveal_result', salons[room_code]['solution'], to=request.sid)

@socketio.on('admin_skip_turn')
def on_admin_skip(data):
    room_code = data.get('room')
    if room_code not in salons:
        return
    emit('log', {'msg': "⚙️ <b>[ADMIN]</b> Bedy a sauté le tour.", 'type': 'admin'}, to=room_code)
    passer_au_tour_suivant(room_code)

@socketio.on('admin_reset_game')
def on_admin_reset(data):
    room_code = data.get('room')
    if room_code in salons:
        emit('forced_reset', to=room_code)
        salons[room_code]['started'] = False

@socketio.on('disconnect')
def handle_disconnect():
    for room_code, game in list(salons.items()):
        if request.sid in game['players']:
            nom = game['players'][request.sid]['name']
            del game['players'][request.sid]
            if request.sid in game['order']:
                game['order'].remove(request.sid)
            emit('log', {'msg': f"🏃 {nom} a quitté le salon.", 'type': 'system'}, to=room_code)
            if not game['players']:
                del salons[room_code]
            elif game['started'] and len(game['order']) > 0:
                game['turn_idx'] = game['turn_idx'] % len(game['order'])
                envoyer_changement_tour(room_code)
            break

if __name__ == '__main__':
