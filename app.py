import os
from datetime import datetime
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO, join_room, send, emit
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'chave-secreta-gaivota-segura'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Força a exclusão do banco de dados antigo corrompido ao iniciar
db_path = os.path.join(app.instance_path, 'database.db')
if os.path.exists(db_path):
    try:
        os.remove(db_path)
    except Exception as e:
        print("Aviso ao limpar banco antigo:", e)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins='*')

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)

class Meeting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    room_code = db.Column(db.String(50), unique=True, nullable=False)
    scheduled_time = db.Column(db.DateTime, nullable=False)
    owner = db.Column(db.String(80), nullable=False)

class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(50), nullable=False)
    message = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class SharedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(50), nullable=False)
    filename = db.Column(db.String(200), nullable=False)
    original_name = db.Column(db.String(200), nullable=False)
    sender = db.Column(db.String(80), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()
    # Cria o usuário admin automático
    if not User.query.filter_by(username='admin').first():
        hashed_admin_pw = generate_password_hash('123')
        admin_user = User(username='admin', password=hashed_admin_pw)
        db.session.add(admin_user)
        db.session.commit()

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        action = request.form.get('action')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            error = "Preencha todos os campos."
        else:
            user = User.query.filter_by(username=username).first()
            if action == 'register':
                if user:
                    error = "Este nome de usuário já existe."
                else:
                    hashed_pw = generate_password_hash(password)
                    new_user = User(username=username, password=hashed_pw)
                    db.session.add(new_user)
                    db.session.commit()
                    session['user'] = username
                    return redirect(url_for('index'))
            elif action == 'login':
                if user and check_password_hash(user.password, password):
                    session['user'] = username
                    return redirect(url_for('index'))
                else:
                    error = "Usuário ou senha incorretos."
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    meetings = Meeting.query.order_by(Meeting.scheduled_time).all()
    return render_template('index.html', meetings=meetings, current_user=session['user'])

@app.route('/create_meeting', methods=['POST'])
def create_meeting():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    title = request.form.get('title')
    room_code = request.form.get('room_code')
    scheduled_time_str = request.form.get('scheduled_time')
    owner = session['user']

    if scheduled_time_str:
        scheduled_time = datetime.strptime(scheduled_time_str, '%Y-%m-%dT%H:%M')
    else:
        scheduled_time = datetime.utcnow()

    new_meeting = Meeting(title=title, room_code=room_code, scheduled_time=scheduled_time, owner=owner)
    db.session.add(new_meeting)
    db.session.commit()
    return redirect(url_for('index'))

@app.route('/delete_meeting/<int:meeting_id>', methods=['POST'])
def delete_meeting(meeting_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    meeting = Meeting.query.get_or_404(meeting_id)
    current_user = session['user']

    if current_user == 'admin' or meeting.owner == current_user:
        db.session.delete(meeting)
        db.session.commit()

    return redirect(url_for('index'))

@app.route('/room/<room_code>')
def room(room_code):
    if 'user' not in session:
        return redirect(url_for('login'))
    meeting = Meeting.query.filter_by(room_code=room_code).first_or_404()
    files = SharedFile.query.filter_by(room_code=room_code).order_by(SharedFile.timestamp.desc()).all()
    feedbacks = Feedback.query.filter_by(room_code=room_code).order_by(Feedback.timestamp.desc()).all()
    return render_template('room.html', meeting=meeting, files=files, feedbacks=feedbacks, current_user=session['user'])

@app.route('/upload/<room_code>', methods=['POST'])
def upload_file(room_code):
    if 'user' not in session:
        return jsonify({'error': 'Não autorizado'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400
    
    file = request.files['file']
    sender = session['user']

    if file.filename == '':
        return jsonify({'error': 'Arquivo inválido'}), 400

    filename = secure_filename(file.filename)
    unique_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
    file.save(file_path)

    new_file = SharedFile(room_code=room_code, filename=unique_name, original_name=filename, sender=sender)
    db.session.add(new_file)
    db.session.commit()

    socketio.emit('new_file', {
        'original_name': filename,
        'sender': sender,
        'url': url_for('static', filename=f'uploads/{unique_name}')
    }, room=room_code)

    return jsonify({'success': True})

@socketio.on('join')
def on_join(data):
    room = data['room']
    username = data['username']
    join_room(room)
    send({'msg': f"{username} entrou na sala.", 'user': 'Sistema'}, room=room)

@socketio.on('text_message')
def handle_message(data):
    room = data['room']
    send({'msg': data['msg'], 'user': data['user']}, room=room)

@socketio.on('send_feedback')
def handle_feedback(data):
    room_code = data['room']
    feedback_text = data['feedback']
    new_fb = Feedback(room_code=room_code, message=feedback_text)
    db.session.add(new_fb)
    db.session.commit()

    emit('new_private_feedback', {
        'message': feedback_text,
        'time': new_fb.timestamp.strftime('%H:%M')
    }, room=room_code)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)