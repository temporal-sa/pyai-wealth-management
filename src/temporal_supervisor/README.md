


./startlocalworker.sh

temporal workflow start --workflow-id ai-chat --type WealthManagementWorkflow --task-queue PY-AI-Supervisor 

temporal workflow signal --workflow-id ai-chat --name end_workflow

temporal workflow start --workflow-id ai-chat --type WealthManagementWorkflow --task-queue PY-AI-Supervisor 

temporal workflow signal --workflow-id ai-chat --name process_user_message --input '{"user_input":"Who are my beneficiaries?"}'

temporal workflow query --workflow-id ai-chat --type get_chat_history

temporal workflow signal --workflow-id ai-chat --name process_user_message --input '{"user_input":"123"}'

temporal workflow signal --workflow-id ai-chat --name process_user_message --input '{"user_input":"what investments do I have?"}'