select * from bedrock_integration.bedrock_kb limit 10;

select * from chat_history ch limit 10;

select * from chat_history ch 
order by ch.created_at desc

select count(ch.id) 
from chat_history ch 