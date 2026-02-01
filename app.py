import os
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from azure.storage.blob import BlobServiceClient
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import uuid

# Load environment variables (for local dev)
load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION ---
# 1. Database Config (Azure PostgreSQL)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 2. Azure Blob Storage Config
AZURE_CONNECTION_STRING = os.environ.get('AZURE_STORAGE_CONNECTION_STRING')
CONTAINER_NAME = os.environ.get('CONTAINER_NAME', 'horse-images')

db = SQLAlchemy(app)

# --- MODEL ---
class Horse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    breed = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(500), nullable=True)

# --- HELPER: Azure Blob Upload ---
def upload_file_to_blob(file):
    if not file:
        return None
    
    try:
        # Create the BlobServiceClient object
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
        
        # Ensure container exists
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        if not container_client.exists():
            container_client.create_container(public_access="blob")

        # Generate unique filename to prevent overwrites
        filename = secure_filename(file.filename)
        unique_filename = str(uuid.uuid4()) + "_" + filename

        # Upload
        blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=unique_filename)
        blob_client.upload_blob(file)

        return blob_client.url
    except Exception as e:
        print(f"Error uploading to Azure: {e}")
        return None

# --- ROUTES ---

@app.route('/')
def index():
    horses = Horse.query.all()
    return render_template('index.html', horses=horses)

@app.route('/create', methods=['GET', 'POST'])
def create():
    if request.method == 'POST':
        name = request.form['name']
        breed = request.form['breed']
        age = request.form['age']
        price = request.form['price']
        image_file = request.files['image']

        # Upload image to Azure Blob
        image_url = upload_file_to_blob(image_file)

        new_horse = Horse(name=name, breed=breed, age=age, price=price, image_url=image_url)
        db.session.add(new_horse)
        db.session.commit()
        return redirect(url_for('index'))
    return render_template('create.html')

@app.route('/edit/<int:id>', methods=['GET', 'POST'])
def edit(id):
    horse = Horse.query.get_or_404(id)
    if request.method == 'POST':
        horse.name = request.form['name']
        horse.breed = request.form['breed']
        horse.age = request.form['age']
        horse.price = request.form['price']

        # Handle new image upload if provided
        if 'image' in request.files and request.files['image'].filename != '':
            new_image_url = upload_file_to_blob(request.files['image'])
            if new_image_url:
                horse.image_url = new_image_url

        db.session.commit()
        return redirect(url_for('index'))
    return render_template('edit.html', horse=horse)

@app.route('/delete/<int:id>')
def delete(id):
    horse = Horse.query.get_or_404(id)
    # Optional: Add logic here to delete blob from Azure if needed
    db.session.delete(horse)
    db.session.commit()
    return redirect(url_for('index'))

# Create DB tables if they don't exist
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)