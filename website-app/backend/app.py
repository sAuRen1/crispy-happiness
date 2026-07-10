import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(base_dir, 'templates'),
    static_folder=os.path.join(base_dir, 'static')
)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///ankety.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'crispy_happiness_super_secret'

db = SQLAlchemy(app)

class Anketa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100))
    phone = db.Column(db.String(30))
    message = db.Column(db.Text)
    date = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        message = request.form.get('message')

        if name and name.strip():
            new_anketa = Anketa(name=name.strip(), email=email, phone=phone, message=message)
            db.session.add(new_anketa)
            db.session.commit()
            
            flash('✅ Спасибо! Ваша анкета отправлена. Мы скоро свяжемся с вами.', 'success')
        else:
            flash('❌ Пожалуйста, заполните хотя бы имя', 'error')
        
        return redirect(url_for('index'))

    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)