import logging
import os
import pathlib
import markdown
import psycopg2
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional # NEW: Import Optional for the optional query parameter

# Setup logger and Azure Monitor:
logger = logging.getLogger("app")
logger.setLevel(logging.INFO)

# Setup FastAPI app:
app = FastAPI()
parent_path = pathlib.Path(__file__).parent.parent
templates = Jinja2Templates(directory=parent_path / "templates")
templates.env.globals["prod"] = os.environ.get("RUNNING_IN_PRODUCTION", False)
# Use relative path for url_for, so that it works behind a proxy like Codespaces
templates.env.globals["url_for"] = app.url_path_for


def get_direct_db_connection():
    try:
        host = os.getenv("POSTGRES_HOST")
        dbname = os.getenv("POSTGRES_DB")
        user = os.getenv("POSTGRES_USER")
        password = os.getenv("POSTGRES_PASSWORD")
        port = int(os.getenv("POSTGRES_PORT", 5432))
    except Exception as e:
        logger.error(f"Missing environment variable: {e}")
        return None

    try:
        conn = psycopg2.connect(
            host=host,
            database=dbname,
            user=user,
            password=password,
            port=port,
            sslmode="require"
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return None

# MODIFIED: The root endpoint now handles both the list and detail views
@app.get("/", response_class=HTMLResponse)
async def index(request: Request, submission_id: Optional[str] = Query(default=None)):
    """
    This endpoint now has two behaviors:
    1. If `submission_id` is NOT provided, it displays a list of all submissions.
    2. If `submission_id` IS provided, it displays the details for that specific submission.
    """
    conn = get_direct_db_connection()
    if not conn:
        logger.error("Failed to connect to the database.")
        return HTMLResponse(content="Error de conexión a la Base de Datos", status_code=500)
    
    cur = conn.cursor()

    # --- BEHAVIOR 1: Show list of all submissions ---
    if not submission_id:
        logger.info("No submission_id provided, fetching all submissions for table view.")
        try:
            # Fetch columns needed for the table view. Let's get id and status.
            # Ordering by a timestamp column would be ideal if you have one.
            cur.execute("SELECT submission_id, status FROM formai_db ORDER BY submission_id DESC")
            all_submissions = cur.fetchall()
            
            # Create a list of dictionaries to pass to the template
            submissions_data = [
                {"id": row[0], "status": row[1]} for row in all_submissions
            ]

            return templates.TemplateResponse("submissions_list.html", {
                "request": request,
                "submissions": submissions_data
            })
        except Exception as e:
            logger.error(f"Error fetching submission list: {e}")
            return HTMLResponse(content="Error al obtener la lista de envíos.", status_code=500)
        finally:
            cur.close()
            conn.close()

    # --- BEHAVIOR 2: Show specific submission details (your original logic) ---
    else:
        logger.info(f"Fetching details for submission ID: {submission_id}")
        try:
            cur.execute(
                "SELECT result_consulting, status FROM formai_db WHERE submission_id = %s",
                (submission_id,)
            )
            row = cur.fetchone()

            if not row:
                logger.warning(f"Submission ID {submission_id} not found in the database.")
                return templates.TemplateResponse("waiting.html", {
                    "request": request,
                    "submission_id": submission_id
                })

            result_consulting, status = row

            if status == "processing":
                # Render a "please wait" page with JS polling
                return templates.TemplateResponse("waiting.html", {
                    "request": request,
                    "submission_id": submission_id
                })

            elif status == "error":
                return HTMLResponse(content="Ha ocurrido un error durante el procesado.", status_code=500)

            # status == "success"
            html_content = markdown.markdown(result_consulting)
            logger.info(f"Retrieved results for submission ID {submission_id}.")
            return templates.TemplateResponse("submission_details.html", {
                "request": request,
                "results_client": html_content
            })
        except Exception as e:
            logger.error(f"Error fetching details for {submission_id}: {e}")
            return HTMLResponse(content=f"Error al obtener los detalles para {submission_id}.", status_code=500)
        finally:
            cur.close()
            conn.close()


@app.get("/check-status", response_class=JSONResponse)
async def check_status(submission_id: str = Query(...)):
    conn = get_direct_db_connection()
    if not conn:
        return JSONResponse({"status": "error", "message": "Database connection error"}, status_code=500)

    cur = conn.cursor()
    cur.execute("SELECT status FROM formai_db WHERE submission_id = %s", (submission_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return JSONResponse({"status": "error", "message": "submission_id not found"}, status_code=404)

    return JSONResponse({"status": row[0]})