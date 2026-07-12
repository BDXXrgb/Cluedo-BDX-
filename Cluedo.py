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

# Structure globale des salons
# { 'ROOM_CODE': { 'players': { sid: {name, cards, piece, eliminated} }, 'order': [sid1, sid2], 'turn_idx': 0, 'solution': {...}, 'started': False } }
salons = {}

@app.route('/')
def index():
    return render_template('index.html')

# --- GESTION DU LOBBY & SALONS ---
@socketio.on('create_game')
def handle_create_game(data):
    username = data.get('username', 'Anonyme')
    # Génère un code de salon unique à 4 lettres
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
    # Renvoie les informations au créateur
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
        emit('error', {'msg': "La partie a déjà commencé !"})
        return
        
    if len(game['players']) >= 6:
        emit('error', {'msg': "Le salon est plein (max 6 joueurs)."})
        return

    # Ajouter le joueur au salon
    game['players'][request.sid] = {
        'name': username,
        'cards': [],
        'piece': 'Hall',
        'eliminated': False
    }
    game['order'].append(request.sid)
    
    join_room(room_code)
    
    # Mettre à jour la liste des joueurs pour tout le monde dans le salon
    liste_noms = [p['name'] for p in game['players'].values()]
    emit('room_update', {'room': room_code, 'players': liste_noms}, to=room_code)

# --- LANCEMENT DE LA PARTIE ---
@socketio.on('start_game')
def handle_start_game(data):
    room_code = data.get('room')
    if room_code not in salons:
        return
        
    game = salons[room_code]
    if len(game['players']) < 2:
        emit('error', {'msg': "Il faut au moins 2 joueurs pour lancer la partie !"})
        return
        
    if game['started']:
        return

    # 1. Sélection de la solution (le crime)
    meurtrier = random.choice(SUSPECTS)
    arme_crime = random.choice(ARMES)
    lieu_crime = random.choice(LIEUX)
    
    game['solution'] = {
        'suspect': meurtrier,
        'arme': arme_crime,
        'lieu': lieu_crime
    }
    
    # 2. Préparation et distribution des cartes restantes
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
    
    # Envoyer à chaque joueur ses cartes de départ respectives
    for sid, p_info in game['players'].items():
        emit('game_started', {'cards': p_info['cards']}, to=sid)
        
    emit('log', {'msg': "🚀 La partie commence ! Trouvez le coupable !"}, to=room_code)
    
    # Placer tous les pions au centre (Hall) initialement
    for sid, p_info in game['players'].items():
        emit('pion_update', {'sid': sid, 'name': p_info['name'], 'piece': 'Hall'}, to=room_code)
        
    envoyer_changement_tour(room_code)

# --- FLUX DE JEU (DÉS & MOUVEMENTS) ---
@socketio.on('lancer_des')
def handle_lancer_des(data):
    room_code = data.get('room')
    if room_code not in salons: return
    
    game = salons[room_code]
    current_turn_sid = game['order'][game['turn_idx']]
    
    if request.sid != current_turn_sid: return
    
    de1 = random.randint(1, 6)
    de2 = random.randint(1, 6)
    total = de1 + de2
    
    emit('des_resultat', {'total': total}, to=request.sid)
    emit('log', {'msg': f"🎲 <b>{game['players'][request.sid]['name']}</b> a lancé les dés et obtenu un total de <b>{total}</b> !"}, to=room_code)

@socketio.on('player_move')
def handle_player_move(data):
    room_code = data.get('room')
    piece = data.get('piece')
    if room_code not in salons: return
    
    game = salons[room_code]
    if request.sid not in game['players']: return
    
    game['players'][request.sid]['piece'] = piece
    
    # Met à jour la position visuelle du rond pour tous les joueurs connectés
    emit('pion_update', {
        'sid': request.sid,
        'name': game['players'][request.sid]['name'],
        'piece': piece
    }, to=room_code)

# --- HYPOTHÈSE (DEMANDE DE CARTE) ---
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
    
    emit('log', {'msg': f"🔍 <b>{demandeur_nom}</b> fait une hypothèse : <i>{suspect}</i> dans le <i>{lieu}</i> avec le <i>{arme}</i>."}, to=room_code)
    
    # Téléporte automatiquement le suspect appelé dans la pièce de l'enquête
    for sid, p_info in game['players'].items():
        if p_info['name'] == suspect:
            p_info['piece'] = lieu
            emit('pion_update', {'sid': sid, 'name': p_info['name'], 'piece': lieu}, to=room_code)
            emit('log', {'msg': f"👤 {suspect} est appelé dans le/la {lieu}."}, to=room_code)

    # Tour de table pour trouver une carte à montrer au demandeur
    idx_demandeur = game['order'].index(demandeur_sid)
    carte_trouvee = None
    joueur_qui_montre = None
    
    for i in range(1, len(game['order'])):
        check_idx = (idx_demandeur + i) % len(game['order'])
        target_sid = game['order'][check_idx]
        target_player = game['players'][target_sid]
        
        # Trouver les cartes correspondantes possédées par ce joueur
        matches = [c for c in target_player['cards'] if c in [suspect, arme, lieu]]
        if matches:
            carte_trouvee = random.choice(matches) # Choisit une carte au hasard parmi ses doublons
            joueur_qui_montre = target_player['name']
            break
            
    if carte_trouvee:
        # 1. Tout le monde sait QUI a montré une carte à QUI
        emit('log', {'msg': f"🃏 <b>{joueur_qui_montre}</b> a montré une carte discrètement à <b>{demandeur_nom}</b>."}, to=room_code)
        # 2. SEUL le demandeur reçoit le nom exact de la vraie carte dévoilée
        emit('hypothese_result', {'demandeurs_uniquement': True, 'carte_devoilee': carte_trouvee}, to=demandeur_sid)
    else:
        emit('log', {'msg': "❌ Personne n'a pu contredire cette hypothèse !"}, to=room_code)
        
    passer_au_tour_suivant(room_code)

# --- ACCUSATION ULTIME ---
@socketio.on('action_accusation')
def handle_accusation(data):
    room_code = data.get('room')
    suspect = data.get('suspect')
    arme = data.get('arme')
    lieu = data.get('lieu')
    
    if room_code not in salons: return
    game = salons[room_code]
    accusateur_sid = request.sid
    nom_accusateur = game['players'][accusateur_sid]['name']
    sol = game['solution']
    
    emit('log', {'msg': f"🚨 <b>{nom_accusateur}</b> PORTE UNE ACCUSATION ULTIME : <b>{suspect}</b>, avec le <b>{arme}</b>, dans le <b>{lieu}</b> !"}, to=room_code)
    
    # Vérification stricte de la solution
    if suspect == sol['suspect'] and arme == sol['arme'] and lieu == sol['lieu']:
        emit('game_over', {'msg': f"🎉 VICTOIRE ! {nom_accusateur} a résolu le crime ! C'était bien {sol['suspect']} avec le {sol['arme']} dans le {sol['lieu']}."}, to=room_code)
        game['started'] = False
    else:
        emit('log', {'msg': f"❌ L'accusation de {nom_accusateur} est FAUSSE ! Il est éliminé des enquêtes."}, to=room_code)
        game['players'][accusateur_sid]['eliminated'] = True
        emit('player_eliminated', to=accusateur_sid)
        
        # Vérifie s'il reste des joueurs actifs
        actifs = [sid for sid, p in game['players'].items() if not p['eliminated']]
        if not actifs:
            emit('game_over', {'msg': f"💀 Fin de partie ! Tout le monde a échoué. Le tueur s'échappe ! C'était : {sol['suspect']} ({sol['arme']} au {sol['lieu']})"}, to=room_code)
            game['started'] = False
        else:
            passer_au_tour_suivant(room_code)

# --- FONCTIONS DE TOUR DE JEU INTERNES ---
def passer_au_tour_suivant(room_code):
    game = salons[room_code]
    if not game['started']: return
    
    # Passer au joueur suivant non éliminé
    while True:
        game['turn_idx'] = (game['turn_idx'] + 1) % len(game['order'])
        next_sid = game['order'][game['turn_idx']]
        if not game['players'][next_sid]['eliminated']:
            break
            
    envoyer_changement_tour(room_code)

def envoyer_changement_tour(room_code):
    game = salons[room_code]
    active_sid = game['order'][game['turn_idx']]
    active_name = game['players'][active_sid]['name']
    
    for sid in game['players'].keys():
        emit('turn_update', {
            'is_your_turn': (sid == active_sid),
            'current_player': active_name
        }, to=sid)

# --- COMMANDES D'ADMINISTRATION SECRÈTES (BEDY) ---
@socketio.on('admin_revive_player')
def on_admin_revive(data):
    room_code = data.get('room')
    target_name = data.get('target_name', '').trim()
    
    if room_code not in salons: return
    game = salons[room_code]
    
    # Recherche du joueur par son pseudo dans la room
    for sid, p_info in game['players'].items():
        if p_info['name'] == target_name:
            p_info['eliminated'] = False
            emit('you_are_revived', to=sid)
            emit('log', {'msg': f"⚙️ <b>[ADMIN]</b> Bedy a ressuscité <b>{target_name}</b> ! Réintégration immédiate."}, to=room_code)
            envoyer_changement_tour(room_code)
            break

@socketio.on('admin_reveal_solution')
def on_admin_reveal(data):
    room_code = data.get('room')
    if room_code not in salons: return
    
    game = salons[room_code]
    sol = game['solution']
    
    # Renvoie le résultat EXCLUSIVEMENT au socket de l'admin Bedy (request.sid)
    if sol:
        emit('admin_reveal_result', {
            'suspect': sol['suspect'],
            'arme': sol['arme'],
            'lieu': sol['lieu']
        }, to=request.sid)

# --- DÉCONNEXION ---
@socketio.on('disconnect')
def handle_disconnect():
    for room_code, game in list(salons.items()):
        if request.sid in game['players']:
            nom = game['players'][request.sid]['name']
            del game['players'][request.sid]
            if request.sid in game['order']:
                game['order'].remove(request.sid)
                
            emit('log', {'msg': f"🏃 {nom} a quitté la partie."}, to=room_code)
            
            # Si le salon est vide, on le supprime
            if not game['players']:
                del salons[room_code]
            else:
                # Réorganiser la liste graphique et les tours
                liste_noms = [p['name'] for p in game['players'].values()]
                emit('room_update', {'room': room_code, 'players': liste_noms}, to=room_code)
                if game['started'] and len(game['order']) > 0:
                    game['turn_idx'] = game['turn_idx'] % len(game['order'])
                    envoyer_changement_tour(room_code)
            break

if __name__ == '__main__':
    socketio.run(app, debug=True)
