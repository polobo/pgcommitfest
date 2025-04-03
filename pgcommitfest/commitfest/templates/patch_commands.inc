<div>
 {%if poc.is_open%}
  <div>
   <div>Set to:</div>
   {%if poc.status != poc.STATUS_REVIEW and poc.status != poc.STATUS_COMMITTER %}
    <div><a class="btn btn-default" href="status/review/">Needs Review</a></div>
   {%endif%}
   {%if poc.status != poc.STATUS_AUTHOR %}
    <div><a class="btn btn-default" href="status/author/">Waiting on Author</a></div>
   {%endif%}
   {%if poc.status != poc.STATUS_COMMITTER and poc.status != poc.STATUS_AUTHOR %}
    <div><a class="btn btn-default" href="status/committer/">Ready for Committer</a></div>
   {%endif%}
  </div>

  {%if not is_author %}
   <div>Review:</div>
   <a class="btn btn-default" href="comment/">Comment</a>
   <a class="btn btn-default" href="review/">Review</a>
  {%endif%}

  <div>
   <div>Move to:</div>
   {%if not cf.isfuture and workflow.future %}
    {%if is_author or request.user.is_staff %}
     <div>
      <a class="btn btn-default"
         href="transition/?fromcfid={{poc.commitfest.id}}&tocfid={{workflow.future.id}}">
      {{workflow.future.name}}</a>
      <span>New Feature patches get moved here.</span>
     </div>
    {%endif%}
   {%endif%}

   {%if not cf.isopen and workflow.open %}
    {%if is_committer or request.user.is_staff %}
     <div>
      <a class="btn btn-default"
         href="transition/?fromcfid={{poc.commitfest.id}}&tocfid={{workflow.open.id}}">
      {{workflow.open.name}}</a>
      <span>Back-Patch qualified patches get moved here.</span>
     </div>
    {%endif%}
   {%endif%}
  </div>

  {%if not cf.isinprogress and workflow.progress %}
   {%if is_committer or request.user.is_staff %}
    <div>
     <a class="btn btn-default"
        href="transition/?fromcfid={{poc.commitfest.id}}&tocfid={{workflow.progress.id}}">
     {{workflow.progress.name}}</a>
     <span>Back-Patch qualified patches get moved here.</span>
    </div>
   {%endif%}
  {%endif%}

  {%if is_committer or is_author %}
   <div>Resolution:</div>
   <div>
    {%if is_committer %}
     <a class="btn btn-default" href="close/committed/" onclick="return flagCommitted({%if patch.committer%}'{{patch.committer}}'{%elif is_committer%}'{{user.username}}'{%else%}null{%endif%})">Commit</a>
     <a class="btn btn-default" href="close/reject/" onclick="return verify_reject()">Reject</a>
    {%endif%}
    {%if is_author %}
     <a class="btn btn-default" href="close/withdrawn/" onclick="return verify_withdrawn()">Withdraw</a>
    {%endif%}
   </div>
  {%endif%}
 {%else%}
  <div>No Workflow Actions on Closed PoCs</div>
 {%endif%}
</div>
