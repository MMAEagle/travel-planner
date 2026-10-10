from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
import psycopg2
import hashlib
import os
import uvicorn

# Connection String Supabase
DB_URL = "postgresql://postgres.ezpzxhsopcdcrpkrzfej:MMAEagle%40123@aws-0-eu-west-1.pooler.supabase.com:5432/postgres?sslmode=require"

app = FastAPI(title="Travel Planner API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    return psycopg2.connect(DB_URL)

# Native hashing με hashlib (χωρίς dependencies bcrypt)
def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    hashed = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return f"{salt}${hashed}"

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, hashed = stored_hash.split("$")
        check_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
        return check_hash == hashed
    except Exception:
        return False

# ================= MODELS =================
class UserAuth(BaseModel):
    username: str
    password: str

class RouteItem(BaseModel):
    day_number: str
    point_a: str
    point_b: str
    transport_mode: str
    duration: str

class TripCreate(BaseModel):
    user_id: int
    city: str
    routes: List[RouteItem]

class RatingItem(BaseModel):
    point_name: str
    rating: float

class SaveRatingsRequest(BaseModel):
    trip_id: int
    ratings: List[RatingItem]

# ================= AUTH ENDPOINTS =================
@app.post("/api/register")
def register(user: UserAuth):
    username = user.username.strip().lower()
    if not username or not user.password:
        raise HTTPException(status_code=400, detail="Το username και ο κωδικός είναι υποχρεωτικά")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM travel_users WHERE username = %s", (username,))
    if c.fetchone():
        c.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Το username υπάρχει ήδη")
    
    hashed_pw = hash_password(user.password)
    c.execute("INSERT INTO travel_users (username, password_hash) VALUES (%s, %s) RETURNING id", (username, hashed_pw))
    user_id = c.fetchone()[0]
    conn.commit()
    c.close()
    conn.close()
    return {"status": "ok", "user_id": user_id, "username": username}

@app.post("/api/login")
def login(user: UserAuth):
    username = user.username.strip().lower()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, password_hash FROM travel_users WHERE username = %s", (username,))
    row = c.fetchone()
    c.close()
    conn.close()
    
    if not row or not verify_password(user.password, row[1]):
        raise HTTPException(status_code=401, detail="Λανθασμένο username ή κωδικός")
    
    return {"status": "ok", "user_id": row[0], "username": username}

# ================= TRIPS & ROUTES ENDPOINTS =================
@app.get("/api/trips/{user_id}")
def get_user_trips(user_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, city FROM travel_trips WHERE user_id = %s ORDER BY id DESC", (user_id,))
    trips = [{"id": r[0], "city": r[1]} for r in c.fetchall()]
    c.close()
    conn.close()
    return {"trips": trips}

@app.post("/api/trips/save")
def save_trip(trip: TripCreate):
    city = trip.city.strip()
    if not city or not trip.routes:
        raise HTTPException(status_code=400, detail="Απαιτείται όνομα πόλης και τουλάχιστον μία διαδρομή")
    
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO travel_trips (user_id, city) VALUES (%s, %s) RETURNING id", (trip.user_id, city))
    trip_id = c.fetchone()[0]
    
    for r in trip.routes:
        c.execute("""
            INSERT INTO travel_routes (trip_id, day_number, point_a, point_b, transport_mode, duration)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (trip_id, r.day_number, r.point_a.strip(), r.point_b.strip(), r.transport_mode, r.duration.strip()))
        
    conn.commit()
    c.close()
    conn.close()
    return {"status": "ok", "trip_id": trip_id}

@app.get("/api/trip/{trip_id}")
def get_trip_details(trip_id: int):
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT city FROM travel_trips WHERE id = %s", (trip_id,))
    trip_row = c.fetchone()
    if not trip_row:
        c.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Το ταξίδι δεν βρέθηκε")
    city = trip_row[0]
    
    c.execute("""
        SELECT id, day_number, point_a, point_b, transport_mode, duration 
        FROM travel_routes WHERE trip_id = %s ORDER BY id ASC
    """, (trip_id,))
    routes = [
        {
            "id": r[0],
            "day": r[1],
            "point_a": r[2],
            "point_b": r[3],
            "transport": r[4],
            "duration": r[5]
        }
        for r in c.fetchall()
    ]
    
    c.execute("SELECT point_name, rating FROM travel_point_ratings WHERE trip_id = %s", (trip_id,))
    ratings_dict = {r[0]: r[1] for r in c.fetchall()}
    
    avg_rating = None
    if ratings_dict:
        avg_rating = round(sum(ratings_dict.values()) / len(ratings_dict), 1)
        
    c.close()
    conn.close()
    return {
        "trip_id": trip_id,
        "city": city,
        "routes": routes,
        "ratings": ratings_dict,
        "avg_rating": avg_rating
    }

# ================= RATINGS ENDPOINT =================
@app.post("/api/trip/ratings")
def save_ratings(data: SaveRatingsRequest):
    conn = get_db()
    c = conn.cursor()
    for item in data.ratings:
        c.execute("""
            INSERT INTO travel_point_ratings (trip_id, point_name, rating)
            VALUES (%s, %s, %s)
            ON CONFLICT (trip_id, point_name) DO UPDATE SET rating = EXCLUDED.rating
        """, (data.trip_id, item.point_name.strip(), item.rating))
        
    conn.commit()
    c.close()
    conn.close()
    return {"status": "ok"}

@app.get("/")
def serve_index():
    return FileResponse("index.html")

if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000)
