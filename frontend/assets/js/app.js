const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const $ = id => document.getElementById(id);
const initData = tg?.initData || "";
let account = null;
let wallet = null;
let referralData = null;
let tasksData = [];

function money(cents){return "$"+((Number(cents||0))/100).toFixed(2)}
function showToast(message){const el=$("toast");el.textContent=message;el.style.display="block";setTimeout(()=>el.style.display="none",3000)}
function escapeHTML(value){return String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}

async function api(path,options={}){
  if(!initData) throw new Error("Open this app inside Telegram to authenticate.");
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json","X-Telegram-Init-Data":initData,...options.headers}});
  const data=await response.json().catch(()=>({}));
  if(!response.ok) throw new Error(data.detail||"Request failed ("+response.status+")");
  return data;
}

function showPage(name){
  document.querySelectorAll(".page").forEach(p=>p.classList.toggle("active",p.id===name));
  document.querySelectorAll(".nav").forEach(b=>b.classList.toggle("active",b.dataset.page===name));
  if(name==="tasks")loadTasks();
  if(name==="earnings")loadEarnings();
  if(name==="referrals")loadReferrals();
  if(name==="wallet")loadWithdrawals();
}
document.querySelectorAll("[data-page]").forEach(b=>b.addEventListener("click",()=>showPage(b.dataset.page)));

async function loadDashboard(){
  try{
    account=await api("/users/me");
    wallet=await api("/users/me/wallet");
    $("username").textContent=account.first_name||account.username||"Miner";
    const balances=wallet.balances||{};
    $("available").textContent=money(balances.USD?.available ?? 0);
    $("pending").textContent=money(balances.USD?.pending ?? 0);
    $("reserved").textContent=money(balances.USD?.reserved ?? 0);
    $("paid").textContent=money(balances.USD?.paid ?? 0);
    $("earnAvailable").textContent=money(balances.USD?.available ?? 0);
    $("earnPending").textContent=money(balances.USD?.pending ?? 0);
    $("accountInfo").innerHTML=`Name: ${escapeHTML(account.first_name||"Miner")}<br>Country: ${escapeHTML(account.country||"Not provided")}<br>Account status: ${escapeHTML(account.status||"active")}<br>Referral code: ${escapeHTML(account.referral_code||"Not available")}`;
  }catch(e){
    if(String(e.message).includes("not registered")){
      try{
        account=await api("/users/register",{method:"POST",body:JSON.stringify({referral_code:tg?.initDataUnsafe?.start_param||null})});
        await loadDashboard();
      }catch(err){$("accountInfo").textContent=err.message;showToast(err.message)}
    }else{$("accountInfo").textContent=e.message;showToast(e.message)}
  }
}

async function loadTasks(){
  const box=$("taskList");box.innerHTML='<div class="panel">Loading tasks...</div>';
  try{
    const data=await api("/tasks");tasksData=data.tasks||[];
    if(!tasksData.length){box.innerHTML='<div class="panel">No active tasks are available right now. Check again later.</div>';return}
    box.innerHTML=tasksData.map(t=>`<article class="panel"><div class="task-title">${escapeHTML(t.title||"Mining task")}</div><p class="muted">${escapeHTML(t.description||"")}</p><div class="task-meta"><span>${escapeHTML(t.category||"General")}</span><b>${money(t.reward_amount??t.reward_cents??t.reward_amount_cents??0)}</b></div><button class="primary" onclick="openTask(${Number(t.id)})">View task</button></article>`).join("");
  }catch(e){box.innerHTML=`<div class="panel">${escapeHTML(e.message)}</div>`}
}
window.openTask=async id=>{
  try{
    const data=await api("/tasks/"+id);const t=data.task;
    const evidence=prompt((t.title||"Task")+"\n\n"+(t.description||"")+"\n\nEnter your completion evidence:");
    if(evidence===null)return;
    await api(`/tasks/${id}/submit`,{method:"POST",body:JSON.stringify({evidence})});
    showToast("Task submitted for review.");loadTasks();
  }catch(e){showToast(e.message)}
};

async function loadEarnings(){
  try{
    const [tx,sub]=await Promise.all([api("/users/me/wallet/transactions"),api("/users/me/submissions")]);
    const transactions=Array.isArray(tx)?tx:(tx.transactions||[]);
    $("transactions").innerHTML=transactions.length?transactions.map(x=>`<div class="panel">${escapeHTML(x.description||x.transaction_type||"Wallet transaction")}<br><span class="muted">${escapeHTML(x.created_at||"")}</span></div>`).join(""):'<div class="panel">No transactions yet.</div>';
    const submissions=Array.isArray(sub)?sub:(sub.submissions||[]);
    $("submissions").innerHTML=submissions.length?submissions.map(x=>`<div class="panel">${escapeHTML(x.task_title||("Task #"+x.task_id))}<br><span class="muted">Status: ${escapeHTML(x.status||"pending")}</span></div>`).join(""):'<div class="panel">No task submissions yet.</div>';
  }catch(e){$("transactions").textContent=e.message}
}

async function loadReferrals(){
  try{
    referralData=await api("/users/me/referrals");
    const count=referralData.total_referrals??referralData.referral_count??referralData.count??0;
    $("referralCount").textContent=count;
    $("referralInfo").innerHTML=`Referral code: <b>${escapeHTML(account?.referral_code||"Not available")}</b><br>Total referrals: ${Number(referralData.total_referrals||0)}<br><span class="muted">Referral rewards are calculated only after eligible revenue settles.</span><br><span class="muted">Rewards are based on eligible settled net revenue.</span>`;
  }catch(e){$("referralInfo").textContent=e.message}
}
$("shareInvite").addEventListener("click",()=>{
  const code=account?.referral_code;
  if(!code){showToast("Referral code is not available.");return}
  const url=location.origin+"/?startapp="+encodeURIComponent(code);
  if(tg?.openTelegramLink)tg.openTelegramLink("https://t.me/share/url?url="+encodeURIComponent(url)+"&text="+encodeURIComponent("Join AdBot Mining Network"));
  else navigator.clipboard?.writeText(url).then(()=>showToast("Invitation link copied."));
});

async function loadWithdrawals(){
  try{
    const data=await api("/users/me/withdrawals");
    const rows=Array.isArray(data)?data:(data.withdrawals||[]);
    $("withdrawals").innerHTML=rows.length?rows.map(w=>`<div class="panel">${money(w.amount_usd_cents ?? w.amount ?? 0)} · ${escapeHTML(w.status)}<br><span class="muted">${escapeHTML(w.requested_at||"")}</span></div>`).join(""):'<div class="panel">No withdrawal requests yet.</div>';
  }catch(e){$("withdrawals").textContent=e.message}
}
$("withdrawButton").addEventListener("click",async()=>{
  const amount=Number($("withdrawAmount").value);
  const address=$("tonAddress").value.trim();
  const msg=$("withdrawMessage");
  if(!Number.isFinite(amount)||amount<2){msg.textContent="Minimum withdrawal is $2.00.";return}
  if(!address){msg.textContent="Enter your public TON wallet address.";return}
  const button=$("withdrawButton");button.disabled=true;button.textContent="Submitting...";
  try{
    await api("/withdrawals",{method:"POST",body:JSON.stringify({amount_usd_cents:Math.round(amount*100),ton_wallet_address:address})});
    msg.textContent="Withdrawal request submitted. It will be reviewed.";
    $("withdrawAmount").value="";await loadDashboard();await loadWithdrawals();
  }catch(e){msg.textContent=e.message}
  finally{button.disabled=false;button.textContent="Request withdrawal"}
});

if(!initData){$("accountInfo").textContent="Open this Mini App from the AdBot Telegram menu to sign in securely."}
else{loadDashboard()}
