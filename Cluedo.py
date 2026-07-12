import random
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret_cluedo_key_12345'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- DONNÉES DU JEU ---
SUSPECTS = ["Mlle Rose", "Colonel Moutarde", "Mme Pervenche", "Docteur Olive", "Mme Leblanc", "Professeur Violet"]
ARMES = ["Chandelier", "Couteau", "Revolver", "Corde", "Matraque", "Clé Anglaise"]
LIEUX = ["Salon", "Véranda", "Salle de Bal", "Salle à Manger", "Cuisine", "Bibliothèque", "Billard", "Bureau", "Hall"]

salons = {}

@app.route('/')
def index():
    return render_template('index.html')

# --- GESTION DU LOBBY & SALONS ---
@socketio.on('create_game')
def handle_create_game(data):
    username = data.get('username', 'Anonyme')
    while True:
        room_code = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ", k=4))
        if room_code not in salons:
            break
            
    salons[room_code] = {
        'players': {
            request.sid: {
                'name': username,
                'cards': [],
                'piece': 'Hall',
                'eliminated': False
            }
        },
        'order': [request.sid],
        'turn_idx': 0,
        'solution': {},
        'started': False
    }
    
    join_room(room_code)
    emit('room_created', {
        'room': room_code,
        'players': [username]
    })

@socketio.on('join_game')
def handle_join_game(data):
    username = data.get('username', 'Anonyme')
    room_code = data.get('room', '').upper()
    
    if room_code not in salons:
        emit('error', {'msg': "Ce code de salon n'existe pas !"})
        return
        
    game = salons[room_code]
    if game['started']:
        emit('error', {'msg': "La partie a déjà commencé ! Utilise le bouton 'Rejoindre en cours' !"})
        return
        
    if len(game['players']) >= 6:
        emit('error', {'msg': "Le salon est plein (max 6 joueurs)."})
        return

    game['players'][request.sid] = {
        'name': username,
        'cards': [],
        'piece': 'Hall',
        'eliminated': False
    }
    game['order'].append(request.sid)
    
    join_room(room_code)
    liste_noms = [p['name'] for p in game['players'].values()]
    emit('room_update', {'room': room_code, 'players': liste_noms}, to=room_code)

# --- REJOINDRE UNE PARTIE EN COURS DE ROUTE ---
@socketio.on('join_in_game')
def handle_join_in_game(data):
    username = data.get('username', 'Anonyme')
    room_code = data.get('room', '').upper()
    
    if room_code not in salons:
        emit('error', {'msg': "Ce code de salon n'existe pas !"})
        return
        
    game = salons[room_code]
    if not game['started']:
        emit('error', {'msg': "La partie n'a pas encore commencé. Fais une connexion normale !"})
        return

    # Intégration en cours de partie
    game['players'][request.sid] = {
        'name': username,
        'cards': [],
        'piece': 'Hall',
        'eliminated': False
    }
    game['order'].append(request.sid)
    join_room(room_code)

    # Voler ou piocher automatiquement des cartes aux autres joueurs pour équilibrer
    toutes_les_cartes_en_jeu = []
    for sid, p in game['players'].items():
        if sid != request.sid:
            toutes_les_cartes_en_jeu.extend(p['cards'])
            
    # Si les autres ont des cartes, on en prend 2 ou 3 au hasard pour le nouveau joueur
    nb_a_donner = min(3, len(toutes_les_cartes_en_jeu) // 2)
    cartes_attribuees = []
    if nb_a_donner > 0:
        for _ in range(nb_a_donner):
            # Choisir un joueur au hasard qui a plus de 1 carte
            donateurs = [sid for sid, p in game['players'].items() if sid != request.sid and len(p['cards']) > 1]
            if donateurs:
                d_sid = random.choice(donateurs)
                c = game['players'][d_sid]['cards'].pop(random.randint(0, len(game['players'][d_sid]['cards'])-1))
                cartes_attribuees.append(c)
                
    game['players'][request.sid]['cards'] = cartes_attribuees

    # Lancer l'interface graphique chez le nouveau joueur
    emit('game_started', {'cards': cartes_attribuees, 'is_rejoin': True}, to=request.sid)
    emit('log', {'msg': f"⚡ <b>{username}</b> a rejoint la partie en cours de route !"}, to=room_code)

    # Actualiser l'affichage de tous les pions pour tout le monde
    for s_id, p_info in game['players'].items():
        emit('pion_update', {'sid': s_id, 'name': p_info['name'], 'piece': p_info['piece']}, to=room_code)

    envoyer_changement_tour(room_code)

# --- LANCEMENT DE LA PARTIE ---
@socketio.on('start_game')
def handle_start_game(data):
    room_code = data.get('room')
    if room_code not in salons: return
    game = salons[room_code]
    
    if len(game['players']) < 2:
        emit('error', {'msg': "Il faut au moins 2 joueurs !"})
        return
    if game['started']: return

    meurtrier = random.choice(SUSPECTS)
    arme_crime = random.choice(ARMES)
    lieu_crime = random.choice(LIEUX)
    
    game['solution'] = {'suspect': meurtrier, 'arme': arme_crime, 'lieu': lieu_crime}
    
    toutes_cartes = SUSPECTS + ARMES + LIEUX
    toutes_cartes.remove(meurtrier)
    toutes_cartes.remove(arme_crime)
    toutes_cartes.remove(lieu_crime)
    random.shuffle(toutes_cartes)
    
    sids = game['order']
    idx = 0
    for carte in toutes_cartes:
        target_sid = sids[idx % len(sids)]
        game['players'][target_sid]['cards'].append(carte)
        idx += 1
        
    game['started'] = True
    for sid, p_info in game['players'].items():
        emit('game_started', {'cards': p_info['cards'], 'is_rejoin': False}, to=sid)
        
    emit('log', {'msg': "🚀 La partie commence ! Trouvez le coupable !"}, to=room_code)
    for sid, p_info in game['players'].items():
        emit('pion_update', {'sid': sid, 'name': p_info['name'], 'piece': 'Hall'}, to=room_code)
        
    envoyer_changement_tour(room_code)

# --- DÉS & MOUVEMENTS ---
@socketio.on('lancer_des')
def handle_lancer_des(data):
    room_code = data.get('room')
    if room_code not in salons: return
    game = salons[room_code]
    if request.sid != game['order'][game['turn_idx']]: return
    
    total = random.randint(1, 6) + random.randint(1, 6)
    emit('des_resultat', {'total': total}, to=request.sid)
    emit('log', {'msg': f"🎲 <b>{game['players'][request.sid]['name']}</b> a obtenu <b>{total}</b> !"}, to=room_code)

@socketio.on('player_move')
def handle_player_move(data):
    room_code = data.get('room')
    piece = data.get('piece')
    if room_code not in salons or request.sid not in salons[room_code]['players']: return
    
    salons[room_code]['players'][request.sid]['piece'] = piece
    emit('pion_update', {'sid': request.sid, 'name': salons[room_code]['players'][request.sid]['name'], 'piece': piece}, to=room_code)

# --- HYPOTHÈSE ---
@socketio.on('action_hypothese')
def handle_hypothese(data):
    room_code = data.get('room')
    suspect = data.get('suspect')
    arme = data.get('arme')
    lieu = data.get('lieu')
    if room_code not in salons: return
    
    game = salons[room_code]
    demandeur_sid = request.sid
    demandeur_nom = game['players'][demandeur_sid]['name']
    
    emit('log', {'msg': f"🔍 <b>{demandeur_nom}</b> soupçonne : <i>{suspect}</i> avec <i>{arme}</i> au <i>{lieu}</i>."}, to=room_code)
    
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
            
    if carte_trouvee:
        emit('log', {'msg': f"🃏 <b>{joueur_qui_montre}</b> a montré une carte à <b>{demandeur_nom}</b>."}, to=room_code)
        emit('hypothese_result', {'demandeurs_uniquement': True, 'carte_devoilee': carte_trouvee}, to=demandeur_sid)
    else:
        emit('log', {'msg': "❌ Personne n'a pu contredire l'hypothèse."}, to=room_code)
        
    passer_au_tour_suivant(room_code)

# --- ACCUSATION ---
@socketio.on('action_accusation')
def handle_accusation(data):
    room_code = data.get('room')
    suspect = data.get('suspect')
    arme = data.get('arme')
    lieu = data.get('lieu')
    if room_code not in salons: return
    
    game = salons[room_code]
    sol = game['solution']
    nom_acc = game['players'][request.sid]['name']
    
    if suspect == sol['suspect'] and arme == sol['arme'] and lieu == sol['lieu']:
        emit('game_over', {'msg': f"🎉 VICTOIRE ! {nom_acc} a trouvé : {sol['suspect']} + {sol['arme']} + {sol['lieu']}."}, to=room_code)
        game['started'] = False
    else:
        emit('log', {'msg': f"❌ L'accusation de {nom_acc} est fausse ! Éliminé."}, to=room_code)
        game['players'][request.sid]['eliminated'] = True
        emit('player_eliminated', to=request.sid)
        
        actifs = [s for s, p in game['players'].items() if not p['eliminated']]
        if not actifs:
            emit('game_over', {'msg': f"💀 Défaite ! Solution : {sol['suspect']} ({sol['arme']} au {sol['lieu']})"}, to=room_code)
            game['started'] = False
        else:
            passer_au_tour_suivant(room_code)

def passer_au_tour_suivant(room_code):
    game = salons[room_code]
    if not game['started']: return
    while True:
        game['turn_idx'] = (game['turn_idx'] + 1) % len(game['order'])
        if not game['players'][game['order'][game['turn_idx']]]['eliminated']:
            break
    envoyer_changement_tour(room_code)

def envoyer_changement_tour(room_code):
    game = salons[room_code]
    active_sid = game['order'][game['turn_idx']]
    for sid in game['players'].keys():
        emit('turn_update', {'is_your_turn': (sid == active_sid), 'current_player': game['players'][active_sid]['name']}, to=sid)

# --- COMMANDES ADMIN SECRÈTES (BEDY) ---
@socketio.on('admin_revive_player')
def on_admin_revive(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').strip()
    if room_code not in salons: return
    
    for sid, p_info in salons[room_code]['players'].items():
        if p_info['name'] == target_name:
            p_info['eliminated'] = False
            emit('you_are_revived', to=sid)
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Bedy a ressuscité <b>{target_name}</b> !"}, to=room_code)
            envoyer_changement_tour(room_code)
            break

@socketio.on('admin_reveal_solution')
def on_admin_reveal(data):
    room_code = data.get('room')
    if room_code in salons and salons[room_code]['solution']:
        emit('admin_reveal_result', salons[room_code]['solution'], to=request.sid)

@socketio.on('admin_skip_turn')
def on_admin_skip(data):
    room_code = data.get('room')
    if room_code not in salons: return
    emit('log', {'msg': "⚙️ <b>[ADMIN]</b> Bedy a passé le tour du joueur."}, to=room_code)
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
            if request.sid in game['order']: game['order'].remove(request.sid)
            emit('log', {'msg': f"🏃 {nom} a quitté la partie."}, to=room_code)
            if not game['players']: del salons[room_code]
            elif game['started'] and len(game['order']) > 0:
                game['turn_idx'] = game['turn_idx'] % len(game['order'])
                envoyer_changement_tour(room_code)
            break

if __name__ == '__main__':
    socketio.run(app, debug=True)
