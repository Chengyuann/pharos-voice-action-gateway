"use strict";

// The public sandbox evaluates example data only. It never builds a wallet transaction.
function previewExample(text, {limit = "5", hour = 12} = {}) {
  if (typeof text !== "string" || text.length > 300) return {status:"blocked", reason:"Use one short payment instruction."};
  if (/\b(?:not|don't|cancel|tomorrow|later|every)\b/i.test(text)) return {status:"blocked",reason:"Use a current, positive payment instruction."};
  const source = text.trim().replace(/[.!?]$/, "").replace(/^(?:I (?:would like|want) to |could you |can you )/i, "");
  const match = /^(?:please )?(?:send|pay|transfer) (\d+(?:\.\d+)?|one|two|three|four|five)(?: or (\d+(?:\.\d+)?|one|two))? (PROS|USDC|WPROS) to ([a-z]+)$/i.exec(source);
  if (!match) return {status:"clarify",reason:"Try “Send 1 USDC to Alice”. Use one exact amount, asset and example contact."};
  const digits = word => ({one:"1",two:"2",three:"3",four:"4",five:"5"}[word.toLowerCase()] || word);
  if (match[2]) return {status:"clarify",reason:`Which exact amount: ${digits(match[1])} or ${digits(match[2])}?`};
  const amount=digits(match[1]), token=match[3].toUpperCase(), recipient=match[4].toLowerCase();
  const decimals=token==="USDC"?6:18;
  function units(value) {
    if (!/^\d+(?:\.\d+)?$/.test(value)) throw new Error("Use a decimal amount.");
    const [whole, fraction=""]=value.split(".");
    if (fraction.length>decimals || whole.length>20) throw new Error("Amount precision exceeds the token limit.");
    return BigInt(whole)*10n**BigInt(decimals)+BigInt(fraction.padEnd(decimals,"0")||"0");
  }
  let raw, maximum;
  try {raw=units(amount);maximum=units(limit);} catch(error){return {status:"blocked",reason:error.message};}
  const checks=[
    {name:"Spend limit",passed:raw>0n&&raw<=maximum,detail:`${amount} / ${limit} ${token}`},
    {name:"Allowed recipient",passed:["alice","bob"].includes(recipient),detail:recipient},
    {name:"Time window",passed:hour>=9&&hour<17,detail:`${String(hour).padStart(2,"0")}:00 UTC · allowed 09:00–17:00`},
  ];
  const failed=checks.find(check=>!check.passed);
  return {status:failed?"blocked":"allowed",reason:failed?`${failed.name} blocked this example.`:"Ready for human review.",amount,token,recipient,checks};
}
if (typeof module!=="undefined") module.exports={previewExample};
if (typeof document!=="undefined") {
  const form=document.getElementById("sandbox-form");
  const output=document.getElementById("sandbox-result");
  form.addEventListener("submit",event=>{
    event.preventDefault();
    const result=previewExample(document.getElementById("sandbox-request").value,{
      limit:document.getElementById("sandbox-limit").value,hour:Number(document.getElementById("sandbox-hour").value),
    });
    output.replaceChildren();
    const heading=document.createElement("h3");heading.textContent=result.status==="allowed"?"Ready to review":result.status==="clarify"?"One more detail":"Request blocked";
    const reason=document.createElement("p");reason.textContent=result.reason;output.append(heading,reason);
    for (const check of result.checks||[]) {
      const row=document.createElement("p");row.className=check.passed?"pass":"fail";
      row.textContent=`${check.passed?"✓":"×"} ${check.name} · ${check.detail}`;output.append(row);
    }
    output.dataset.status=result.status;
  });
}
