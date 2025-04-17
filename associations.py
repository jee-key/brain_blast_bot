import random
import logging
import asyncio
import os
import json
from typing import Dict, List, Set, Optional, Tuple
from pathlib import Path
import csv
import time
from collections import defaultdict

# LRU Cache implementation for Python versions < 3.8
# (Python 3.8+ provides functools.lru_cache with maxsize parameter)
class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache = {}
        self.lru = {}
        self.counter = 0

    def get(self, key):
        if key in self.cache:
            self.counter += 1
            self.lru[key] = self.counter
            return self.cache[key]
        return None

    def put(self, key, value):
        self.counter += 1
        if len(self.cache) >= self.capacity:
            # Find least recently used
            old_key = min(self.lru.items(), key=lambda x: x[1])[0]
            del self.cache[old_key]
            del self.lru[old_key]
        self.cache[key] = value
        self.lru[key] = self.counter

# Constants
SESSION_TIMEOUT = 1800  # 30 minutes in seconds
MAX_HISTORY_SIZE = 50   # Maximum number of words to keep in history
CACHE_SIZE = 1000       # Number of words to keep in memory

# User session storage with minimal memory footprint
drift_sessions: Dict[int, Dict] = {}

# Common starter words - kept in memory as they're small and frequently used
STARTER_WORDS = [
    "снег", "море", "солнце", "музыка", "книга", "дерево", "кошка", "собака", 
    "город", "дом", "человек", "дорога", "цветок", "небо", "звезда", "огонь", 
    "вода", "мечта", "любовь", "время", "еда", "сон", "телефон", "компьютер", 
    "машина", "самолёт", "поезд", "река", "гора", "лес", "друг", "семья", 
    "праздник", "игра", "спорт", "театр", "кино", "искусство", "наука", "школа",
    "университет", "работа", "деньги", "мысль", "идея", "история", "будущее",
    "лето", "зима", "весна", "осень", "утро", "вечер", "ночь", "день"
]

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

ASSOCIATIONS_CSV = BASE_DIR / "associations_data.csv"
ASSOCIATION_SIM_CSV = BASE_DIR / "association_data.csv"

# Replace global dictionaries with LRU caches for memory efficiency
large_assoc_cache = LRUCache(CACHE_SIZE)
sim_assoc_cache = LRUCache(CACHE_SIZE)

# Load core associations once (small enough to keep in memory)
CORE_ASSOCIATIONS = {}

def load_large_associations(word: str) -> List[str]:
    """Lazily load associations for a specific word only when needed"""
    # Check cache first
    cached = large_assoc_cache.get(word)
    if cached is not None:
        return cached
    
    if not ASSOCIATIONS_CSV.exists():
        return []
        
    result = []
    try:
        with open(ASSOCIATIONS_CSV, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or ',' not in line:
                    continue
                parts = line.split(',')
                if len(parts) < 2:
                    continue
                stimulus = parts[0].strip().lower()
                response = parts[1].strip().lower()
                
                if stimulus == word:
                    result.append(response)
        
        # Cache the result
        large_assoc_cache.put(word, result)
        return result
    except Exception as e:
        logging.error(f"Error loading large associations for '{word}': {e}")
        return []

def load_sim_associations(word: str) -> List[Tuple[float, str]]:
    """Lazily load similarity associations for a specific word"""
    # Check cache first
    cached = sim_assoc_cache.get(word)
    if cached is not None:
        return cached
        
    if not ASSOCIATION_SIM_CSV.exists():
        return []
        
    result = []
    try:
        with open(ASSOCIATION_SIM_CSV, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 3:
                    continue
                w1, w2, sim = parts
                try:
                    sim = float(sim)
                    w1 = w1.lower()
                    if w1 == word:
                        result.append((sim, w2.lower()))
                except ValueError:
                    continue
        
        # Sort by similarity (highest first)
        result.sort(reverse=True)
        
        # Cache the result
        sim_assoc_cache.put(word, result)
        return result
    except Exception as e:
        logging.error(f"Error loading sim associations for '{word}': {e}")
        return []

def load_core_associations():
    """Load the core associations that are small enough to keep in memory"""
    assoc_path = MODELS_DIR / "associations.json"
    
    if (assoc_path.exists()):
        try:
            with open(assoc_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading associations file: {e}")
    
    # Core associations are small enough to keep in memory
    core_associations = {
        "деньги": ["кошелек", "банк", "зарплата", "богатство", "валюта", "работа", "бизнес"],
        "кошелек": ["деньги", "карта", "монеты", "купюры", "платеж", "покупка", "сумка"],
        
        "дерево": ["лес", "ветка", "листья", "корни", "ствол", "крона", "природа", "тень"],
        "листья": ["дерево", "зеленый", "осень", "ветка", "лес", "растение", "опадать"],
        "крона": ["дерево", "ветка", "листья", "верхушка", "птица", "высота", "тень"],
        
        "часы": ["время", "минута", "стрелка", "циферблат", "будильник", "утро", "ночь"],
        "будильник": ["утро", "звонок", "проснуться", "часы", "сон", "рано", "время"],
        "утро": ["день", "солнце", "рассвет", "будильник", "завтрак", "кофе", "начало"],
        
        "солнце": ["тепло", "свет", "лето", "небо", "день", "яркое", "лучи", "жара"],
        "дождь": ["вода", "капли", "зонт", "мокрый", "гроза", "тучи", "осень", "холод"],
        "холод": ["зима", "мороз", "снег", "лед", "куртка", "дрожь", "теплая одежда", "туман"],
        "туман": ["утро", "облако", "видимость", "влажность", "дымка", "серый", "молоко"],
        
        "молоко": ["белый", "корова", "напиток", "завтрак", "кофе", "каша", "туман", "сыр"],
        "банан": ["фрукт", "желтый", "обезьяна", "тропики", "сладкий", "яблоко", "еда"],
        "яблоко": ["фрукт", "красный", "зеленый", "сад", "сок", "компот", "банан", "груша"],
        "сыр": ["молоко", "желтый", "бутерброд", "мышь", "вино", "снек", "еда"],
        
        "мышь": ["компьютер", "курсор", "провод", "клик", "сыр", "грызун", "маленькая"],
        "компьютер": ["интернет", "работа", "экран", "программа", "игра", "мышь", "клавиатура"]
    }
    
    try:
        with open(assoc_path, 'w', encoding='utf-8') as f:
            json.dump(core_associations, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Error saving associations file: {e}")
    
    return core_associations

# Load core associations at startup (small enough to keep in memory)
CORE_ASSOCIATIONS = load_core_associations()

def get_large_associations(word: str) -> list:
    """Get associations from large dataset, loading on demand"""
    word = word.lower().strip()
    return load_large_associations(word)

def get_sim_associations(word: str, used_words: Set[str], topn: int = 5) -> list:
    """Get similarity-based associations, loading on demand"""
    word = word.lower().strip()
    
    # Get similarity associations (lazy loaded)
    sim_assocs = load_sim_associations(word)
    
    # Filter out used words and limit to topn
    return [w2 for _, w2 in sim_assocs if w2 not in used_words][:topn]

def get_random_starter_word() -> str:
    """Returns a random word to start an association chain"""
    return random.choice(STARTER_WORDS)

def get_association(word: str, used_words: Set[str]) -> str:
    """Get the best semantic association for a word, with memory efficiency in mind"""
    word = word.lower().strip()
    logging.info(f"Finding association for: '{word}'")
    
    # Try similarity-based associations first (loaded on demand)
    sim_assoc = get_sim_associations(word, used_words)
    if sim_assoc:
        selected = sim_assoc[0]
        logging.info(f"Selected sim association: '{word}' → '{selected}'")
        return selected

    # Try large local associations (loaded on demand)
    large_assoc = get_large_associations(word)
    available_large = [w for w in large_assoc if w not in used_words]
    if available_large:
        selected = random.choice(available_large)  # Use random to diversify
        logging.info(f"Selected large local association: '{word}' → '{selected}'")
        return selected

    # Try core associations (already in memory)
    if word in CORE_ASSOCIATIONS:
        core_options = [w for w in CORE_ASSOCIATIONS[word] if w not in used_words]
        if core_options:
            selected = random.choice(core_options)
            logging.info(f"Selected core association: '{word}' → '{selected}'")
            return selected
    
    # Try finding associations from same category
    for category_word, category_words in CORE_ASSOCIATIONS.items():
        if word in category_words:
            options = [w for w in category_words if w not in used_words] + [category_word]
            if options:
                selected = random.choice(options)
                logging.info(f"Selected association from same category: '{word}' → '{selected}'")
                return selected
    
    # Fall back to starter words
    unused_starters = [w for w in STARTER_WORDS if w not in used_words]
    if unused_starters:
        selected = random.choice(unused_starters)
        logging.info(f"Selected random starter word: '{word}' → '{selected}'")
        return selected
    
    # Last resort
    return random.choice(STARTER_WORDS)

def cleanup_sessions():
    """Remove inactive sessions to free up memory"""
    current_time = time.time()
    expired_users = []
    
    for user_id, session in drift_sessions.items():
        # Get session start time
        start_time = session.get("start_time", 0)
        
        # If session is older than SESSION_TIMEOUT, mark for removal
        if current_time - start_time > SESSION_TIMEOUT:
            expired_users.append(user_id)
    
    # Remove expired sessions
    for user_id in expired_users:
        chain = drift_sessions[user_id].get("chain", [])
        logging.info(f"Cleaning up expired session for user {user_id} with chain length {len(chain)}")
        del drift_sessions[user_id]
    
    return len(expired_users)

def start_drift_session(user_id: int) -> str:
    """Start a new association chain for a user with memory efficiency in mind"""
    # Clean up old sessions to free memory
    cleanup_sessions()
    
    starter_word = get_random_starter_word()
    
    drift_sessions[user_id] = {
        "chain": [starter_word],
        "last_word": starter_word,
        "start_time": time.time(),  # Use time.time() instead of asyncio loop time for simplicity
        "history": set([starter_word])  # Track used words to avoid repetition
    }
    
    return starter_word

def limit_history_size(history: Set[str], max_size: int = MAX_HISTORY_SIZE) -> Set[str]:
    """Limit the history size to prevent unbounded memory growth"""
    if len(history) <= max_size:
        return history
    
    # Keep only the most recent words
    return set(list(history)[-max_size:])

def add_association(user_id: int, user_input: str) -> str:
    """Process user input and generate next association with memory efficiency"""
    # Start new session if needed
    if user_id not in drift_sessions:
        return start_drift_session(user_id)
    
    session = drift_sessions[user_id]
    chain = session["chain"]
    
    # Update last activity time to prevent premature cleanup
    session["start_time"] = time.time()
    
    # Process user input
    user_input = user_input.lower().strip()
    
    # Add user's word to the chain
    chain.append(user_input)
    session["history"].add(user_input)
    
    # Limit history size to prevent memory growth
    if len(session["history"]) > MAX_HISTORY_SIZE:
        session["history"] = limit_history_size(session["history"])
    
    # Extract key term from user input
    user_terms = user_input.split()
    
    if len(user_terms) == 1:
        key_term = user_terms[0]
    else:
        # For multi-word input, check if any word is in our vocabulary
        known_terms = [term for term in user_terms if 
                      term in CORE_ASSOCIATIONS or 
                      any(term in CORE_ASSOCIATIONS.get(k, []) for k in CORE_ASSOCIATIONS)]
        
        if known_terms:
            key_term = known_terms[0]
        else:
            key_term = max(user_terms, key=len)
    
    # Get next association
    next_word = get_association(key_term, session["history"])
    
    # Update session
    chain.append(next_word)
    session["last_word"] = next_word
    session["history"].add(next_word)
    
    return next_word

def stop_drift_session(user_id: int) -> List[str]:
    """End a drift session and return the complete chain"""
    if user_id not in drift_sessions:
        return []
    
    chain = drift_sessions[user_id]["chain"]
    
    logging.info(f"User {user_id} completed drift chain: {' → '.join(chain)}")
    
    # Clean up memory
    del drift_sessions[user_id]
    
    return chain

# Schedule periodic cleanup to run in the background
async def periodic_cleanup(interval_seconds=600):  # 10 minutes
    """Periodically clean up expired sessions"""
    while True:
        try:
            cleaned = cleanup_sessions()
            if cleaned > 0:
                logging.info(f"Periodic cleanup removed {cleaned} expired sessions")
            
            # Also clean caches if they're getting too large
            if len(large_assoc_cache.cache) > CACHE_SIZE * 0.9:
                large_assoc_cache.cache.clear()
                large_assoc_cache.lru.clear()
                logging.info("Cleared large association cache")
                
            if len(sim_assoc_cache.cache) > CACHE_SIZE * 0.9:
                sim_assoc_cache.cache.clear()
                sim_assoc_cache.lru.clear()
                logging.info("Cleared similarity association cache")
                
        except Exception as e:
            logging.error(f"Error during periodic cleanup: {e}")
            
        await asyncio.sleep(interval_seconds)

# Start periodic cleanup task when module is imported
def start_cleanup_task():
    loop = asyncio.get_event_loop()
    try:
        loop.create_task(periodic_cleanup())
        logging.info("Started periodic session cleanup task")
    except Exception as e:
        logging.error(f"Failed to start cleanup task: {e}")

# Start the cleanup task if running in a context with an event loop
try:
    start_cleanup_task()
except RuntimeError:
    logging.info("No event loop available, cleanup task will start when event loop runs")