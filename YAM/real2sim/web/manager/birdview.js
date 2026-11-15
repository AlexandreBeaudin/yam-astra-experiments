'use strict';
// Orthographic metric table editor. No image homography or guessed pixel-to-world mapping.
class BirdviewEditor {
 constructor(canvas,onChange){this.canvas=canvas;this.onChange=onChange;this.objects=[];this.active=0;this.drag=null;this.undoStack=[];
  canvas.addEventListener('pointerdown',e=>this.down(e));canvas.addEventListener('pointermove',e=>this.move(e));canvas.addEventListener('pointerup',e=>this.up(e));canvas.addEventListener('pointercancel',e=>this.up(e));
 }
 setBundle(bundle,layout){this.bundle=bundle;this.original=structuredClone(layout);this.objects=[];this.undoStack=[];this.drag=null;this.active=0;this.readonly=layout.bowl_m!==undefined;
  if(layout.bowl_m){const t=bundle.task;this.bounds=t.table_bounds_xy.map(a=>[...a]);this.axes=['X','Y'];this.objects=[{id:'bowl',name:'研钵',x:layout.bowl_m[0],y:layout.bowl_m[1],angle:0,radius:.065,rotatable:false},{id:'pestle',name:'研杵',x:layout.pestle_m[0],y:layout.pestle_m[1],angle:layout.pestle_yaw_deg,width:.110,height:.045,offset:0,rotatable:false}];}
  else if(bundle.platform_id==='xtrainer'&&layout.goal_m){const t=bundle.task;this.bounds=t.table_bounds_xy.map(a=>[...a]);this.axes=['X','Y'];this.objects=[{id:'goal',name:'球门',x:layout.goal_m[0],y:layout.goal_m[1],angle:layout.goal_yaw_deg,width:t.goal_depth_m,height:t.goal_width_m,offset:t.goal_depth_m/2,rotatable:true},{id:'ball',name:'球',x:layout.ball_m[0],y:layout.ball_m[1],angle:0,radius:t.ball_radius_m,rotatable:false}];}
  else if(bundle.platform_id==='yam'&&layout.objects&&bundle.generation_config?.provisional_workspace_uv_m){this.bounds=bundle.generation_config.provisional_workspace_uv_m.map(a=>[...a]);this.axes=['u','v'];this.objects=layout.objects.map(o=>({id:o.material_id,name:o.name,x:o.uv_m[0],y:o.uv_m[1],angle:o.yaw_deg,width:o.size_m[0],height:o.size_m[1],offset:0,rotatable:o.material_id!=='MAT-00012'}));}
  else{this.bounds=[[-1,1],[-1,1]];this.axes=['X','Y'];}
  this.draw();
 }
 scale(){const [bx,by]=this.bounds;return Math.min((this.canvas.width-100)/(bx[1]-bx[0]),(this.canvas.height-100)/(by[1]-by[0]));}
 pixel(x,y){const [bx,by]=this.bounds,s=this.scale();return [this.canvas.width/2+(x-(bx[0]+bx[1])/2)*s,this.canvas.height/2-(y-(by[0]+by[1])/2)*s];}
 world(x,y){const [bx,by]=this.bounds,s=this.scale();return [(x-this.canvas.width/2)/s+(bx[0]+bx[1])/2,-(y-this.canvas.height/2)/s+(by[0]+by[1])/2];}
 event(e){const r=this.canvas.getBoundingClientRect();return [(e.clientX-r.left)*this.canvas.width/r.width,(e.clientY-r.top)*this.canvas.height/r.height];}
 polygon(o){const a=o.angle*Math.PI/180,c=Math.cos(a),s=Math.sin(a),w=o.width/2,h=o.height/2;return [[-w,-h],[w,-h],[w,h],[-w,h]].map(([x,y])=>[o.x+c*(x+(o.offset||0))-s*y,o.y+s*(x+(o.offset||0))+c*y]);}
 handle(o){const a=o.angle*Math.PI/180,r=(o.width||.06)+(o.offset||0)+.035;return this.pixel(o.x+r*Math.cos(a),o.y+r*Math.sin(a));}
 hit(o,x,y){if(o.radius)return Math.hypot(x-o.x,y-o.y)<=o.radius+.008;const a=o.angle*Math.PI/180,c=Math.cos(a),s=Math.sin(a),dx=x-o.x,dy=y-o.y;return Math.abs(c*dx+s*dy-(o.offset||0))<=o.width/2+.008&&Math.abs(-s*dx+c*dy)<=o.height/2+.008;}
 snapshot(){return structuredClone(this.objects);}
 down(e){if(this.readonly||e.button!==0)return;const p=this.event(e),[x,y]=this.world(...p),active=this.objects[this.active];let mode='move',index=-1;
  if(active?.rotatable&&Math.hypot(p[0]-this.handle(active)[0],p[1]-this.handle(active)[1])<14){index=this.active;mode='rotate';}
  else for(let i=this.objects.length-1;i>=0;i--)if(this.hit(this.objects[i],x,y)){index=i;break;}
  if(index<0)return;e.preventDefault();this.active=index;const o=this.objects[index];this.drag={mode,dx:x-o.x,dy:y-o.y,original:this.snapshot(),changed:false};this.canvas.setPointerCapture(e.pointerId);this.draw();
 }
 move(e){if(!this.drag)return;if(!this.drag.changed){this.undoStack.push(this.drag.original);this.drag.changed=true;}const [x,y]=this.world(...this.event(e)),o=this.objects[this.active];
  if(this.drag.mode==='rotate'){let a=Math.atan2(y-o.y,x-o.x)*180/Math.PI;if(e.shiftKey)a=Math.round(a/5)*5;o.angle=a;}
  else{let nx=x-this.drag.dx,ny=y-this.drag.dy;if(e.shiftKey){nx=Math.round(nx/.005)*.005;ny=Math.round(ny/.005)*.005;}o.x=nx;o.y=ny;}
  this.draw();this.onChange(this.changes());
 }
 up(e){this.drag=null;if(this.canvas.hasPointerCapture(e.pointerId))this.canvas.releasePointerCapture(e.pointerId);}
 rotate(degrees){if(this.readonly)return;const o=this.objects[this.active];if(!o?.rotatable)return;this.undoStack.push(this.snapshot());o.angle=(o.angle+degrees+540)%360-180;this.draw();this.onChange(this.changes());}
 undo(){if(!this.undoStack.length)return;this.objects=this.undoStack.pop();this.draw();this.onChange(this.changes());}
 changes(){if(this.bundle?.platform_id==='xtrainer'){const g=this.objects[0],b=this.objects[1];return {goal_x_m:g.x,goal_y_m:g.y,goal_yaw_deg:g.angle,ball_x_m:b.x,ball_y_m:b.y};}
  return {objects:this.objects.map(o=>({material_id:o.id,u_m:o.x,v_m:o.y,yaw_deg:o.angle}))};}
 fromFields(values){for(const o of this.objects){if(this.bundle.platform_id==='xtrainer'){o.x=values[o.id+'_x_m'];o.y=values[o.id+'_y_m'];if(o.rotatable)o.angle=values.goal_yaw_deg;}else{o.x=values[o.id+'|u_m'];o.y=values[o.id+'|v_m'];o.angle=values[o.id+'|yaw_deg'];}}this.draw();}
 draw(){const c=this.canvas,ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);ctx.fillStyle='#152331';ctx.fillRect(0,0,c.width,c.height);ctx.font='15px sans-serif';if(!this.objects.length){ctx.fillStyle='white';ctx.fillText('此任务尚未配置平面编辑器',24,35);return;}
  const [bx,by]=this.bounds;ctx.strokeStyle='#304557';ctx.lineWidth=1;
  for(let x=Math.ceil(bx[0]/.05)*.05;x<=bx[1];x+=.05){const p=this.pixel(x,by[0]),q=this.pixel(x,by[1]);ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...q);ctx.stroke();}
  for(let y=Math.ceil(by[0]/.05)*.05;y<=by[1];y+=.05){ctx.beginPath();ctx.moveTo(...this.pixel(bx[0],y));ctx.lineTo(...this.pixel(bx[1],y));ctx.stroke();}
  const tl=this.pixel(bx[0],by[1]),br=this.pixel(bx[1],by[0]);ctx.strokeStyle='#d9e4f0';ctx.strokeRect(tl[0],tl[1],br[0]-tl[0],br[1]-tl[1]);
  ctx.fillStyle='#b5c6d7';ctx.fillText(`${this.axes[0]} →   ${this.axes[1]} ↑ · 网格 5 cm · 正交俯视`,20,25);
  this.objects.forEach((o,i)=>{const color=i===this.active?'#ffce6e':'#79d4ff',p=this.pixel(o.x,o.y);ctx.strokeStyle=color;ctx.fillStyle=color+'55';ctx.lineWidth=i===this.active?3:2;ctx.beginPath();if(o.radius)ctx.arc(...p,o.radius*this.scale(),0,Math.PI*2);else this.polygon(o).forEach((v,j)=>{const q=this.pixel(...v);j?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.closePath();ctx.fill();ctx.stroke();ctx.fillStyle=color;ctx.fillText(o.name,p[0]+10,p[1]-12);
   // Goal origin is front-post midpoint; arrow points into goal.
   if(o.rotatable){const a=o.angle*Math.PI/180,q=this.pixel(o.x+.035*Math.cos(a),o.y+.035*Math.sin(a));ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...q);ctx.stroke();}
   if(i===this.active&&o.rotatable){const h=this.handle(o);ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(...h);ctx.stroke();ctx.setLineDash([]);ctx.beginPath();ctx.arc(...h,8,0,2*Math.PI);ctx.fill();ctx.fillText('旋转',h[0]+12,h[1]);}
  });
  const o=this.objects[this.active];ctx.fillStyle='#e7edf5';ctx.fillText(`${o.name}  ${this.axes[0]}=${o.x.toFixed(3)} m  ${this.axes[1]}=${o.y.toFixed(3)} m${o.rotatable?'  '+o.angle.toFixed(1)+'°':''}`,20,c.height-20);
 }
}
window.BirdviewEditor=BirdviewEditor;
