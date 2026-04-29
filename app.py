import os
import json
import ollama
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from dotenv import load_dotenv

# --- INITIALIZATION ---
load_dotenv()
app = Flask(__name__)

# Twilio Client Setup
twilio_client = Client(
    os.getenv('TWILIO_ACCOUNT_SID'), 
    os.getenv('TWILIO_AUTH_TOKEN')
)

# Database Setup
DATA_FOLDER = "data"
DB_FILE = os.path.join(DATA_FOLDER, "sessions.json")
if not os.path.exists(DATA_FOLDER):
    os.makedirs(DATA_FOLDER)

# Medicine Inventory
MEDICINE_STOCK = ["Paracetamol", "Ibuprofen", "Amoxicillin", "Vitamin C", "Cough Syrup", "Azithromycin", "Cetirizine"]

# --- DATABASE HELPERS ---
def load_sessions():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except:
            return {}
    return {}

def save_sessions(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

# Load existing users into memory at start
user_sessions = load_sessions()

# --- FORMATTING HELPERS ---
def format_final_receipt(session):
    receipt = f"*📦 NEW ORDER RECEIVED*\n"
    receipt += f"👤 *Customer Name:* {session.get('name', 'Unknown')}\n"
    receipt += f"🏥 *Clinic/Centre:* {session.get('clinic_name', 'Unknown')}\n"
    receipt += f"--------------------------\n"
    receipt += "*Items Ordered:*\n"
    for item in session.get('cart', []):
        receipt += f"- {item['medicine']} | Qty: {item['qty']}\n"
    receipt += f"--------------------------\n"
    receipt += "_Please contact for manual invoicing._"
    return receipt

# --- MAIN WEBHOOK ---
@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.values.get('Body', '').strip()
    print(f"DEBUG: Received message: {incoming_msg}")
    global user_sessions
    incoming_msg = request.values.get('Body', '').strip()
    sender = request.values.get('From', '')
    
    resp = MessagingResponse()
    reply = resp.message()

    # 1. FORCE RELOAD SESSIONS (To ensure sync between restarts)
    user_sessions = load_sessions()

    # 2. SESSION INITIALIZATION / CHECKOUT RESET LOGIC
    if sender not in user_sessions or incoming_msg.lower().startswith("join"):
        user_sessions[sender] = {
            "state": "GET_NAME", 
            "name": "", 
            "clinic_name": "", 
            "cart": [], 
            "last_selected": None
        }
        save_sessions(user_sessions)
        reply.body("👋 Welcome to the Medical Ordering System!\n\nTo begin, please reply with your **Full Name**:")
        return str(resp)

    session = user_sessions[sender]

    # --- STATE MACHINE ---

    # PHASE 1: Capture Name (Must happen if name is empty)
    if session["state"] == "GET_NAME" or not session["name"]:
        session["name"] = incoming_msg
        session["state"] = "GET_CLINIC"
        save_sessions(user_sessions)
        reply.body(f"Thank you, {incoming_msg}! Now, what is the name of your **Clinic or Medical Centre**?")
        return str(resp)

    # PHASE 2: Capture Clinic/Centre
    if session["state"] == "GET_CLINIC" or not session["clinic_name"]:
        session["clinic_name"] = incoming_msg
        session["state"] = "IDLE"
        save_sessions(user_sessions)
        reply.body(f"Perfect. Registration complete for *{incoming_msg}*.\n\nYou can now search for medicines. What are you looking for today?")
        return str(resp)

    # --- ORDERING LOGIC ---

    # Command: Checkout
    if incoming_msg.lower() == "checkout":
        if not session["cart"]:
            reply.body("Your cart is empty! Please search for a medicine first.")
        else:
            final_receipt = format_final_receipt(session)
            try:
                twilio_client.messages.create(
                    from_=os.getenv('TWILIO_WA_NUMBER'),
                    body=final_receipt,
                    to=os.getenv('MY_BUSINESS_NUMBER')
                )
                reply.body(f"✅ Order confirmed, {session['name']}! Your request for {session['clinic_name']} has been sent to the office.\n\nType a new medicine name to start another order or 'Checkout' again to send updates.")
                session["cart"] = [] # Reset cart after checkout
                save_sessions(user_sessions)
            except Exception as e:
                reply.body("⚠️ Error sending receipt. Check your .env credentials.")
        return str(resp)

    # Command: Add Quantity
    if session["state"] == "ASK_QTY":
        if incoming_msg.isdigit():
            session["cart"].append({"medicine": session["last_selected"], "qty": incoming_msg})
            session["state"] = "IDLE"
            session["last_selected"] = None
            save_sessions(user_sessions)
            reply.body(f"Added! Would you like to add more or type **'Checkout'** to finish?")
        else:
            reply.body("Please enter a valid number for the quantity.")
        return str(resp)

    # Command: Search Medicine
    matches = [m for m in MEDICINE_STOCK if incoming_msg.lower() in m.lower()]
    if matches:
        session["last_selected"] = matches[0]
        session["state"] = "ASK_QTY"
        save_sessions(user_sessions)
        reply.body(f"Found: {matches[0]}. How many units do you need?")
    else:
        # Use AI for normal talk
        try:
            ai_resp = ollama.chat(model='qwen2.5:1.5b', messages=[
                {'role': 'system', 'content': 'You are a pharmacy assistant. Be very brief.'},
                {'role': 'user', 'content': incoming_msg}
            ])
            reply.body(ai_resp['message']['content'])
        except:
            reply.body("Medicine not found. Try searching for 'Para' or 'Amoxicillin'.")

    return str(resp)

if __name__ == "__main__":
    app.run(port=5000)