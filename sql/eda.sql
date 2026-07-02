select * from bedrock_integration.bedrock_kb limit 10;

select * from chat_history ch limit 10;

select * from chat_history ch 
order by ch.created_at desc

select count(ch.id) 
from chat_history ch 

select * from chat_history_metadata chm limit 10;


select * from chat_conversations cc
order by cc.updated_at desc 
limit 20

select * from chat_turn_events cte limit 10;

select * from document_chunks dc limit 10;