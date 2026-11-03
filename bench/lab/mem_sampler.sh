#!/bin/bash
# Usage: mem_sampler.sh OUT.csv INTERVAL_S LLAMA_DIR   (runs until killed; read-only system sampling)
out="$1"; interval="${2:-2}"; llama_dir="$3"
echo "epoch,free,active,inactive,speculative,wired,compressor,purgeable,anonymous,file_backed,swap_used_mb,mp_free_pct,llama_rss_kb,llama_procs" > "$out"
while :; do
  vm=$(/usr/bin/vm_stat | /usr/bin/awk -F: '
    /Pages free/{f=$2} /Pages active/{a=$2} /Pages inactive/{i=$2} /Pages speculative/{s=$2}
    /Pages wired down/{w=$2} /occupied by compressor/{c=$2} /Pages purgeable/{p=$2} /Anonymous pages/{an=$2} /File-backed pages/{fb=$2}
    END{gsub(/[ .]/,"",f);gsub(/[ .]/,"",a);gsub(/[ .]/,"",i);gsub(/[ .]/,"",s);gsub(/[ .]/,"",w);gsub(/[ .]/,"",c);gsub(/[ .]/,"",p);gsub(/[ .]/,"",an);gsub(/[ .]/,"",fb);
        printf "%s,%s,%s,%s,%s,%s,%s,%s,%s",f,a,i,s,w,c,p,an,fb}')
  swap=$(/usr/sbin/sysctl -n vm.swapusage | /usr/bin/awk '{for(n=1;n<=NF;n++) if($n=="used"){v=$(n+2); u=substr(v,length(v)); v=substr(v,1,length(v)-1); if(u=="G") v*=1024; if(u=="K") v/=1024; print v}}')
  mp=$(/usr/bin/memory_pressure -Q 2>/dev/null | /usr/bin/awk -F': ' '/percentage/{gsub(/%/,"",$2); print $2}')
  ll=$(/bin/ps -axo rss=,comm= | /usr/bin/awk -v d="$llama_dir/llama-" 'index($2,d)==1{s+=$1;n++} END{printf "%d,%d",s,n}')
  echo "$(/bin/date +%s),$vm,$swap,$mp,$ll" >> "$out"
  sleep "$interval"
done
