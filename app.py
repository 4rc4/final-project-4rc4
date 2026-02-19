import os
import uuid
from datetime import datetime

from dotenv import load_dotenv
from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    abort,
)
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from azure.storage.blob import BlobServiceClient

# Load environment variables (for local dev)
load_dotenv()

app = Flask(__name__)

# --- CONFIGURATION ---
# Required env vars on Azure:
# DATABASE_URL, AZURE_STORAGE_CONNECTION_STRING, SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

AZURE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER_NAME = os.environ.get("CONTAINER_NAME", "horse-images")

db = SQLAlchemy(app)

# --- LOGIN ---
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

# --- MODELS ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(190), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="buyer")  # buyer / seller / admin
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    horses = db.relationship("Horse", backref="seller", lazy=True)
    orders = db.relationship("Order", backref="buyer", lazy=True)

    def set_password(self, raw_password: str):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)


class Horse(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(100), nullable=False)
    breed = db.Column(db.String(100), nullable=False)
    age = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)

    # NEW: marketplace fields
    description = db.Column(db.Text, nullable=True)
    location = db.Column(db.String(120), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="AVAILABLE")  # AVAILABLE / SOLD
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    image_url = db.Column(db.String(600), nullable=True)

    # NEW: ownership
    seller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    # One horse can be purchased once
    order = db.relationship("Order", backref="horse", uselist=False)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    horse_id = db.Column(db.Integer, db.ForeignKey("horse.id"), nullable=False, unique=True)

    price_at_purchase = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="PAID")  # PAID / CANCELLED
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    # Checkout details (simple demo, not full e-commerce)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(300), nullable=False)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- HELPERS ---
def upload_file_to_blob(file):
    if not file or file.filename == "":
        return None

    if not AZURE_CONNECTION_STRING:
        # If you forgot env vars on Azure, upload will fail gracefully.
        return None

    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)

        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
        if not container_client.exists():
            container_client.create_container(public_access="blob")

        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4()}_{filename}"

        blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=unique_filename)
        blob_client.upload_blob(file, overwrite=True)

        return blob_client.url
    except Exception as e:
        print(f"Error uploading to Azure: {e}")
        return None


def seller_required():
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role not in ("seller", "admin"):
        abort(403)


def can_edit_horse(horse: Horse) -> bool:
    if not current_user.is_authenticated:
        return False
    if current_user.role == "admin":
        return True
    return horse.seller_id == current_user.id


# --- ROUTES ---
@app.route("/")
def index():
    # Show ONLY available horses to feel like a real marketplace
    horses = (
        Horse.query.filter_by(status="AVAILABLE")
        .order_by(Horse.created_at.desc())
        .all()
    )
    return render_template("index.html", horses=horses)


@app.route("/horse/<int:horse_id>")
def horse_detail(horse_id):
    horse = Horse.query.get_or_404(horse_id)
    return render_template("horse_detail.html", horse=horse)


# --- AUTH ---
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "buyer")

        if not email or not password:
            flash("Email and password are required.", "danger")
            return redirect(url_for("register"))

        if role not in ("buyer", "seller"):
            role = "buyer"

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("This email is already registered. Please log in.", "warning")
            return redirect(url_for("login"))

        user = User(email=email, role=role)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        login_user(user)
        flash("Account created successfully.", "success")
        return redirect(url_for("index"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            flash("Invalid email or password.", "danger")
            return redirect(url_for("login"))

        login_user(user)
        flash("Welcome back!", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


# --- SELLER: CREATE LISTING ---
@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    seller_required()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        breed = request.form.get("breed", "").strip()
        age = request.form.get("age", "").strip()
        price = request.form.get("price", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        image_file = request.files.get("image")

        if not name or not breed or not age or not price:
            flash("Name, breed, age and price are required.", "danger")
            return redirect(url_for("sell"))

        try:
            age_int = int(age)
            price_float = float(price)
        except ValueError:
            flash("Age must be a number and price must be a valid number.", "danger")
            return redirect(url_for("sell"))

        image_url = upload_file_to_blob(image_file)

        horse = Horse(
            name=name,
            breed=breed,
            age=age_int,
            price=price_float,
            description=description or None,
            location=location or None,
            image_url=image_url,
            status="AVAILABLE",
            seller_id=current_user.id,
        )

        db.session.add(horse)
        db.session.commit()

        flash("Listing published.", "success")
        return redirect(url_for("horse_detail", horse_id=horse.id))

    return render_template("sell.html")


@app.route("/my-listings")
@login_required
def my_listings():
    seller_required()
    horses = Horse.query.filter_by(seller_id=current_user.id).order_by(Horse.created_at.desc()).all()
    return render_template("my_listings.html", horses=horses)


@app.route("/edit/<int:horse_id>", methods=["GET", "POST"])
@login_required
def edit(horse_id):
    horse = Horse.query.get_or_404(horse_id)
    if not can_edit_horse(horse):
        abort(403)

    if request.method == "POST":
        horse.name = request.form.get("name", "").strip()
        horse.breed = request.form.get("breed", "").strip()

        try:
            horse.age = int(request.form.get("age", "0"))
            horse.price = float(request.form.get("price", "0"))
        except ValueError:
            flash("Age and price must be valid numbers.", "danger")
            return redirect(url_for("edit", horse_id=horse.id))

        horse.description = request.form.get("description", "").strip() or None
        horse.location = request.form.get("location", "").strip() or None

        # Optional new image upload
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            new_url = upload_file_to_blob(image_file)
            if new_url:
                horse.image_url = new_url

        db.session.commit()
        flash("Listing updated.", "success")
        return redirect(url_for("horse_detail", horse_id=horse.id))

    return render_template("edit.html", horse=horse)


@app.route("/delete/<int:horse_id>", methods=["POST"])
@login_required
def delete(horse_id):
    horse = Horse.query.get_or_404(horse_id)
    if not can_edit_horse(horse):
        abort(403)

    # Optional: block delete if sold
    if horse.status == "SOLD":
        flash("You can't delete a SOLD listing.", "warning")
        return redirect(url_for("my_listings"))

    db.session.delete(horse)
    db.session.commit()
    flash("Listing deleted.", "info")
    return redirect(url_for("my_listings"))


# --- BUY FLOW (DIRECT PURCHASE) ---
@app.route("/checkout/<int:horse_id>", methods=["GET", "POST"])
@login_required
def checkout(horse_id):
    horse = Horse.query.get_or_404(horse_id)

    if horse.status != "AVAILABLE":
        flash("This horse is not available.", "warning")
        return redirect(url_for("horse_detail", horse_id=horse.id))

    # Sellers can't buy their own listing (optional but feels real)
    if horse.seller_id == current_user.id:
        flash("You can't buy your own listing.", "warning")
        return redirect(url_for("horse_detail", horse_id=horse.id))

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        if not full_name or not address:
            flash("Full name and address are required.", "danger")
            return redirect(url_for("checkout", horse_id=horse.id))

        # Create order + mark horse SOLD (simple atomic flow)
        order = Order(
            buyer_id=current_user.id,
            horse_id=horse.id,
            price_at_purchase=horse.price,
            full_name=full_name,
            phone=phone or None,
            address=address,
            status="PAID",
        )
        horse.status = "SOLD"

        db.session.add(order)
        db.session.commit()

        flash("Purchase completed! 🎉", "success")
        return redirect(url_for("order_detail", order_id=order.id))

    return render_template("checkout.html", horse=horse)


@app.route("/my-orders")
@login_required
def my_orders():
    orders = Order.query.filter_by(buyer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("my_orders.html", orders=orders)


@app.route("/order/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)

    # Only buyer or admin can see
    if current_user.role != "admin" and order.buyer_id != current_user.id:
        abort(403)

    return render_template("order_detail.html", order=order)


# Create DB tables if they don't exist
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
