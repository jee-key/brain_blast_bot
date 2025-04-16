import random
import logging
import asyncio
import os
import json
from typing import Dict, List, Set
from pathlib import Path
import csv

# Dictionary to store user association sessions
drift_sessions: Dict[int, Dict] = {}

# Core vocabulary for starter words
STARTER_WORDS = [
    "снег", "море", "солнце", "музыка", "книга", "дерево", "кошка", "собака", 
    "город", "дом", "человек", "дорога", "цветок", "небо", "звезда", "огонь", 
    "вода", "мечта", "любовь", "время", "еда", "сон", "телефон", "компьютер", 
    "машина", "самолёт", "поезд", "река", "гора", "лес", "друг", "семья", 
    "праздник", "игра", "спорт", "театр", "кино", "искусство", "наука", "школа",
    "университет", "работа", "деньги", "мысль", "идея", "история", "будущее",
    "лето", "зима", "весна", "осень", "утро", "вечер", "ночь", "день"
]

# Path to store downloaded models
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(exist_ok=True)

# Path to local associations CSV (download from sociation.org)
ASSOCIATIONS_CSV = BASE_DIR / "associations_data.csv"

# Path to new association CSV (word-i word-j similarity-ij)
ASSOCIATION_SIM_CSV = BASE_DIR / "association_data.csv"

# Large associations dict loaded from CSV
LARGE_ASSOCIATIONS = {}

# Dict: word -> list of (similarity, associated_word)
SIM_ASSOCIATIONS = {}

def load_large_associations():
    """
    Loads associations from a CSV file (format: word1,word2,sim)
    Returns a dict: {word1: [word2, ...]}
    """
    if not ASSOCIATIONS_CSV.exists():
        return {}
    associations = {}
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
                if not stimulus or not response:
                    continue
                associations.setdefault(stimulus, []).append(response)
    except Exception as e:
        logging.error(f"Error loading large associations CSV: {e}")
    return associations

def load_sim_associations():
    """
    Loads associations from a CSV file with format: word-i word-j similarity-ij
    Returns a dict: {word-i: [(similarity, word-j), ...]}
    """
    if not ASSOCIATION_SIM_CSV.exists():
        return {}
    sim_assoc = {}
    try:
        with open(ASSOCIATION_SIM_CSV, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 3:
                    continue
                w1, w2, sim = parts
                try:
                    sim = float(sim)
                except ValueError:
                    continue
                w1, w2 = w1.lower(), w2.lower()
                sim_assoc.setdefault(w1, []).append((sim, w2))
    except Exception as e:
        logging.error(f"Error loading sim associations CSV: {e}")
    for w in sim_assoc:
        sim_assoc[w].sort(reverse=True)
    return sim_assoc

# Load large associations if available
LARGE_ASSOCIATIONS = load_large_associations()

# Load similarity-based associations if available
SIM_ASSOCIATIONS = load_sim_associations()

def download_word_associations():
    """
    Downloads or loads word associations from an external source.
    We'll use a combination of pre-defined associations and RusVectores for dynamic associations.
    """
    # Path to local associations file
    assoc_path = MODELS_DIR / "associations.json"
    
    # If we already have associations, load them
    if (assoc_path.exists()):
        try:
            with open(assoc_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading associations file: {e}")
    
    # Otherwise create a basic file with core associations
    # These will serve as fallback when API is unavailable
    core_associations = {
        # Money and wallet
        "деньги": ["кошелек", "банк", "зарплата", "богатство", "валюта", "работа", "бизнес"],
        "кошелек": ["деньги", "карта", "монеты", "купюры", "платеж", "покупка", "сумка"],
        
        # Trees and nature
        "дерево": ["лес", "ветка", "листья", "корни", "ствол", "крона", "природа", "тень"],
        "листья": ["дерево", "зеленый", "осень", "ветка", "лес", "растение", "опадать"],
        "крона": ["дерево", "ветка", "листья", "верхушка", "птица", "высота", "тень"],
        
        # Time and clocks
        "часы": ["время", "минута", "стрелка", "циферблат", "будильник", "утро", "ночь"],
        "будильник": ["утро", "звонок", "проснуться", "часы", "сон", "рано", "время"],
        "утро": ["день", "солнце", "рассвет", "будильник", "завтрак", "кофе", "начало"],
        
        # Weather
        "солнце": ["тепло", "свет", "лето", "небо", "день", "яркое", "лучи", "жара"],
        "дождь": ["вода", "капли", "зонт", "мокрый", "гроза", "тучи", "осень", "холод"],
        "холод": ["зима", "мороз", "снег", "лед", "куртка", "дрожь", "теплая одежда", "туман"],
        "туман": ["утро", "облако", "видимость", "влажность", "дымка", "серый", "молоко"],
        
        # Food
        "молоко": ["белый", "корова", "напиток", "завтрак", "кофе", "каша", "туман", "сыр"],
        "банан": ["фрукт", "желтый", "обезьяна", "тропики", "сладкий", "яблоко", "еда"],
        "яблоко": ["фрукт", "красный", "зеленый", "сад", "сок", "компот", "банан", "груша"],
        "сыр": ["молоко", "желтый", "бутерброд", "мышь", "вино", "снек", "еда"],
        
        # Technology
        "мышь": ["компьютер", "курсор", "провод", "клик", "сыр", "грызун", "маленькая"],
        "компьютер": ["интернет", "работа", "экран", "программа", "игра", "мышь", "клавиатура"]
    }
    
    # Save to file
    try:
        with open(assoc_path, 'w', encoding='utf-8') as f:
            json.dump(core_associations, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Error saving associations file: {e}")
    
    return core_associations

# Load or download core associations
CORE_ASSOCIATIONS = download_word_associations()

def get_large_associations(word: str) -> list:
    """Get associations from the large local CSV-based dictionary."""
    word = word.lower().strip()
    return LARGE_ASSOCIATIONS.get(word, [])

def get_sim_associations(word: str, used_words: Set[str], topn: int = 5) -> list:
    """Get top associated words for a word from SIM_ASSOCIATIONS, excluding used_words."""
    word = word.lower().strip()
    if word not in SIM_ASSOCIATIONS:
        return []
    # Exclude used words
    return [w2 for sim, w2 in SIM_ASSOCIATIONS[word] if w2 not in used_words][:topn]

def get_random_starter_word() -> str:
    """Returns a random word to start an association chain"""
    return random.choice(STARTER_WORDS)

def get_association(word: str, used_words: Set[str]) -> str:
    """
    Gets the best semantic association for a word, avoiding previously used words.
    Uses RusVectores when available, falls back to core associations if needed.
    """
    # Normalize input
    word = word.lower().strip()
    
    # Log the request
    logging.info(f"Finding association for: '{word}'")
    
    # Try association_data.csv (similarity-based) first
    sim_assoc = get_sim_associations(word, used_words)
    if sim_assoc:
        selected = sim_assoc[0]
        logging.info(f"Selected sim association: '{word}' → '{selected}'")
        return selected

    # Try large local associations first
    large_assoc = get_large_associations(word)
    available_large = [w for w in large_assoc if w not in used_words]
    if available_large:
        selected = available_large[0]
        logging.info(f"Selected large local association: '{word}' → '{selected}'")
        return selected

    # Try core associations
    if word in CORE_ASSOCIATIONS:
        core_options = [w for w in CORE_ASSOCIATIONS[word] if w not in used_words]
        if core_options:
            selected = random.choice(core_options)
            logging.info(f"Selected core association: '{word}' → '{selected}'")
            return selected
    
    # If all else fails, find a word from the same category
    for category_words in CORE_ASSOCIATIONS.values():
        if word in category_words:
            options = [w for w in category_words if w not in used_words]
            if options:
                selected = random.choice(options)
                logging.info(f"Selected association from same category: '{word}' → '{selected}'")
                return selected
    
    # Last resort - pick a random unused starter word
    unused_starters = [w for w in STARTER_WORDS if w not in used_words]
    if unused_starters:
        selected = random.choice(unused_starters)
        logging.info(f"Selected random starter word: '{word}' → '{selected}'")
        return selected
    
    # If somehow all words have been used, reset with a random starter
    return random.choice(STARTER_WORDS)

def start_drift_session(user_id: int) -> str:
    """Starts a new drift session for a user and returns the first word"""
    starter_word = get_random_starter_word()
    
    drift_sessions[user_id] = {
        "chain": [starter_word],
        "last_word": starter_word,
        "start_time": asyncio.get_event_loop().time(),
        "history": set([starter_word])  # Track all words used in this session
    }
    
    return starter_word

def add_association(user_id: int, user_input: str) -> str:
    """
    Processes the user's input, generates the next association,
    and updates the drift session.
    """
    if user_id not in drift_sessions:
        # If there's no active session, start a new one
        return start_drift_session(user_id)
    
    session = drift_sessions[user_id]
    chain = session["chain"]
    
    # Clean user input but preserve multi-word phrases
    user_input = user_input.lower().strip()
    
    # Add user's input to the chain
    chain.append(user_input)
    session["history"].add(user_input)
    
    # Extract key term from user input
    user_terms = user_input.split()
    
    # Try multiple strategies to find the most meaningful word
    if len(user_terms) == 1:
        # Single word input - use directly
        key_term = user_terms[0]
    else:
        # For multi-word input, check if any word is in our core vocabulary
        known_terms = [term for term in user_terms if 
                      term in CORE_ASSOCIATIONS or 
                      any(term in assoc_list for assoc_list in CORE_ASSOCIATIONS.values())]
        
        if known_terms:
            key_term = known_terms[0]  # Use the first known term
        else:
            # Otherwise use the longest word as it's likely more significant
            key_term = max(user_terms, key=len)
    
    # Get the next association, avoiding words already used in this session
    next_word = get_association(key_term, session["history"])
    
    # Update the session
    chain.append(next_word)
    session["last_word"] = next_word
    session["history"].add(next_word)
    
    return next_word

def stop_drift_session(user_id: int) -> List[str]:
    """Stops the drift session and returns the full chain of associations"""
    if user_id not in drift_sessions:
        return []
    
    chain = drift_sessions[user_id]["chain"]
    
    # Log the chain
    logging.info(f"User {user_id} completed drift chain: {' → '.join(chain)}")
    
    # Clean up
    del drift_sessions[user_id]
    
    return chain