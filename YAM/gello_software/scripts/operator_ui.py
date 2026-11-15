"""Presentation adapter for the operator workbench; native controls retain authority."""
import json

def enhance_collector(html):
    if 'id="collection"' not in html:
        return html
    labels={
        '准备下一条（机械臂将移动）':'开始采集',
        '结束本条并标注':'结束采集',
        '只读，请取得操作权限':'另一页面正在操作，请关闭该页面后等待自动连接',
        '本页面可操作 · 终端仍可操作':'操作已就绪',
        '停止任务会废弃当前未保存数据并请求安全保持。关闭网页不会停止任务。电机操作仍在终端完成。':'开始采集后先慢速对齐，再按扳机提示操作；显示“录制中”才会保存数据。',
    }
    for old,new in labels.items():
        html=html.replace(json.dumps(old)[1:-1],json.dumps(new)[1:-1])
    # Auto-claim only uses the existing button and its eligibility check. Never steals a lease.
    extra='''<style>#collection{display:none}body{padding-bottom:24px}h1{font-size:20px!important}main{max-width:none!important}article.main{grid-column:auto!important}.view img{max-height:40vh!important}</style>
<script>(()=>{setInterval(()=>{if(document.hidden)return;const button=document.querySelector('[data-action="claim"]');if(button&&!button.disabled)button.click();},700)})();</script>'''
    return html.replace('</html>',extra+'</html>')
