from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime
from enum import Enum


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Enums
class TaskStatus(str, Enum):
    TODO = "To Do"
    IN_PROGRESS = "In Progress"
    DONE = "Done"

class TaskPriority(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# Define Models
class StatusCheck(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class StatusCheckCreate(BaseModel):
    client_name: str

class Subtask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    completed: bool = False

class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = ""
    due_date: Optional[datetime] = None
    priority: TaskPriority = TaskPriority.MEDIUM
    category: Optional[str] = ""
    status: TaskStatus = TaskStatus.TODO
    subtasks: List[Subtask] = []
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    due_date: Optional[datetime] = None
    priority: TaskPriority = TaskPriority.MEDIUM
    category: Optional[str] = ""
    status: TaskStatus = TaskStatus.TODO
    subtasks: List[Subtask] = []

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    priority: Optional[TaskPriority] = None
    category: Optional[str] = None
    status: Optional[TaskStatus] = None
    subtasks: Optional[List[Subtask]] = None


# Original routes
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.dict()
    status_obj = StatusCheck(**status_dict)
    _ = await db.status_checks.insert_one(status_obj.dict())
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    status_checks = await db.status_checks.find().to_list(1000)
    return [StatusCheck(**status_check) for status_check in status_checks]


# Task Management Routes
@api_router.post("/tasks", response_model=Task)
async def create_task(task_input: TaskCreate):
    """Create a new task"""
    task_dict = task_input.dict()
    task_obj = Task(**task_dict)
    result = await db.tasks.insert_one(task_obj.dict())
    if result.inserted_id:
        return task_obj
    raise HTTPException(status_code=500, detail="Failed to create task")

@api_router.get("/tasks", response_model=List[Task])
async def get_tasks(
    status: Optional[TaskStatus] = None,
    priority: Optional[TaskPriority] = None,
    category: Optional[str] = None
):
    """Get all tasks with optional filtering"""
    filter_dict = {}
    if status:
        filter_dict["status"] = status
    if priority:
        filter_dict["priority"] = priority
    if category:
        filter_dict["category"] = category
    
    tasks = await db.tasks.find(filter_dict).sort("created_at", -1).to_list(1000)
    return [Task(**task) for task in tasks]

@api_router.get("/tasks/{task_id}", response_model=Task)
async def get_task(task_id: str):
    """Get a specific task by ID"""
    task = await db.tasks.find_one({"id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return Task(**task)

@api_router.put("/tasks/{task_id}", response_model=Task)
async def update_task(task_id: str, task_update: TaskUpdate):
    """Update a task"""
    task = await db.tasks.find_one({"id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    update_dict = {k: v for k, v in task_update.dict().items() if v is not None}
    update_dict["updated_at"] = datetime.utcnow()
    
    result = await db.tasks.update_one(
        {"id": task_id},
        {"$set": update_dict}
    )
    
    if result.modified_count == 1:
        updated_task = await db.tasks.find_one({"id": task_id})
        return Task(**updated_task)
    
    raise HTTPException(status_code=500, detail="Failed to update task")

@api_router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """Delete a task"""
    result = await db.tasks.delete_one({"id": task_id})
    if result.deleted_count == 1:
        return {"message": "Task deleted successfully"}
    raise HTTPException(status_code=404, detail="Task not found")

@api_router.get("/tasks/stats/dashboard")
async def get_dashboard_stats():
    """Get dashboard statistics"""
    total_tasks = await db.tasks.count_documents({})
    todo_count = await db.tasks.count_documents({"status": TaskStatus.TODO})
    in_progress_count = await db.tasks.count_documents({"status": TaskStatus.IN_PROGRESS})
    done_count = await db.tasks.count_documents({"status": TaskStatus.DONE})
    
    high_priority = await db.tasks.count_documents({"priority": TaskPriority.HIGH})
    overdue_count = await db.tasks.count_documents({
        "due_date": {"$lt": datetime.utcnow()},
        "status": {"$ne": TaskStatus.DONE}
    })
    
    return {
        "total_tasks": total_tasks,
        "todo_count": todo_count,
        "in_progress_count": in_progress_count,
        "done_count": done_count,
        "high_priority_count": high_priority,
        "overdue_count": overdue_count
    }

@api_router.put("/tasks/{task_id}/subtasks/{subtask_id}")
async def update_subtask(task_id: str, subtask_id: str, completed: bool):
    """Update a specific subtask within a task"""
    task = await db.tasks.find_one({"id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Update the specific subtask
    result = await db.tasks.update_one(
        {"id": task_id, "subtasks.id": subtask_id},
        {"$set": {"subtasks.$.completed": completed, "updated_at": datetime.utcnow()}}
    )
    
    if result.modified_count == 1:
        updated_task = await db.tasks.find_one({"id": task_id})
        return Task(**updated_task)
    
    raise HTTPException(status_code=404, detail="Subtask not found")


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()