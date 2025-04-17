import requests
import xml.etree.ElementTree as ET
import re
from datetime import datetime

def get_random_question():
    url = "https://db.chgk.info/xml/random/questions"
    
    try:
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"[parser] HTTP error: {response.status_code}")
            return {
                "question": f"⚠️ Ошибка загрузки вопроса (HTTP {response.status_code})",
                "answer": "ошибка",
                "comment": ""
            }

        try:
            tree = ET.fromstring(response.content)
            question_el = tree.find("question")

            if question_el is None:
                print("[parser] <question> element not found in XML.")
                raise ValueError("Invalid XML: <question> is missing")

            question_text = question_el.findtext("Question", "").strip()
            answer = question_el.findtext("Answer", "").strip()
            comment = question_el.findtext("Comments", "").strip()
            question_id = question_el.findtext("QuestionId", "").strip()
            
            tournament = question_el.findtext("tournamentTitle", "").strip()
            tour = question_el.findtext("tour", "").strip()
            author = question_el.findtext("Authors", "").strip()
            source = question_el.findtext("Source", "").strip()
            question_type = question_el.findtext("Type", "").strip()
            difficulty = question_el.findtext("Difficulty", "").strip()
            teams_total = question_el.findtext("teamsNum", "").strip()
            teams_got_points = question_el.findtext("teamsGotPoints", "").strip()
            
            date_str = question_el.findtext("tourPlayedAt", "").strip()
            formatted_date = ""
            if date_str:
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    formatted_date = date_obj.strftime("%d.%m.%Y")
                except ValueError:
                    formatted_date = date_str

            if not question_text or not answer:
                raise ValueError("Empty question or answer")
            
            image_matches = list(re.finditer(r'\(pic:\s*(.*?)\s*\)', question_text))
            image_urls = []
            
            if image_matches:
                for match in image_matches:
                    image_filename = match.group(1)
                    image_url = f"https://db.chgk.info/images/db/{image_filename}"
                    image_urls.append(image_url)
                
                question_text = re.sub(r'\(pic:\s*.*?\s*\)', '', question_text)
            
            metadata = []
            if tournament:
                metadata.append(f"🏆 Турнир: {tournament}")
            if tour:
                metadata.append(f"📋 Тур: {tour}")
            if author:
                metadata.append(f"✍️ Автор: {author}")
            if formatted_date:
                metadata.append(f"📅 Дата: {formatted_date}")
            if source:
                metadata.append(f"📚 Источник: {source}")
            if difficulty:
                metadata.append(f"🔥 Сложность: {difficulty}")
            if question_type:
                metadata.append(f"📝 Тип: {question_type}")
                
            if teams_total and teams_got_points:
                try:
                    total = int(teams_total)
                    correct = int(teams_got_points)
                    percentage = round((correct / total) * 100) if total > 0 else 0
                    metadata.append(f"📊 Статистика: {correct} из {total} команд ({percentage}%)")
                except (ValueError, ZeroDivisionError):
                    pass
            
            metadata_text = "\n".join(metadata) if metadata else ""
            
            question_url = ""
            tour_file_name = question_el.findtext("tourFileName", "").strip()
            number = question_el.findtext("Number", "").strip()
            
            if tour_file_name and number:
                question_url = f"https://db.chgk.info/question/{tour_file_name}/{number}"
            
            return {
                "question": question_text,
                "answer": answer,
                "comment": comment,
                "question_id": question_id,
                "image_urls": image_urls,
                "tournament": tournament,
                "tour": tour,
                "author": author,
                "date": formatted_date,
                "source": source,
                "difficulty": difficulty,
                "question_type": question_type,
                "teams_stats": f"{teams_got_points}/{teams_total}" if teams_total and teams_got_points else "",
                "metadata_text": metadata_text,
                "question_url": question_url
            }

        except ET.ParseError as e:
            print(f"[parser] XML parse error: {e}")
            return {
                "question": "❌ Could not parse XML. The site may be temporarily unavailable.",
                "answer": "error",
                "comment": f"XML error: {e}"
            }

    except Exception as e:
        print(f"[parser] Error getting question: {e}")
        return {
            "question": "❌ Could not fetch question. Please try again later.",
            "answer": "error",
            "comment": str(e)
        }
