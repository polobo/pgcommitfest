<div class="workflow">
 <div class="row">
  <div class="col-md-3 column"><strong>Change To</strong></div>
  <div class="col-md-2 column"><strong>Annotate</strong></div>
  <div class="col-md-3 column"><strong>Move To</strong></div>
  <div class="col-md-2 column"><strong>Resolve</strong></div>
 </div>
 <!--  Change ---------------- -->
 <div class="row">
  <div class="col-md-3 column">
   {%if poc.status != poc.STATUS_REVIEW and poc.status != poc.STATUS_COMMITTER %}
    <a class="btn btn-default" href="status/review/">Reviewer</a>
   {%endif%}
   {%if poc.status != poc.STATUS_AUTHOR %}
    <a class="btn btn-default" href="status/author/">Author</a>
   {%endif%}
   {%if poc.status != poc.STATUS_COMMITTER and poc.status != poc.STATUS_AUTHOR %}
    <a class="btn btn-default" href="status/committer/">Committer</a>
   {%endif%}
  </div>
   <!--  Annotate ---------------- -->
  <div class="col-md-2 column">
   <a class="btn btn-default" href="comment/">Comment</a>
   <a class="btn btn-default" href="review/">Review</a>
  </div>
   <!--  Move ---------------- -->
  <div class="col-md-3 column">
   {%if not cf.isfuture and workflow.future %}
    {%if is_author or request.user.is_staff %}
     <!--  Future ---------------- -->
     <a class="btn btn-default"
        href="transition/?fromcfid={{poc.commitfest.id}}&tocfid={{workflow.future.id}}">
     {{workflow.future.name}}</a>
    {%endif%}
   {%endif%}

   {%if not cf.isopen and workflow.open %}
    {%if is_committer or request.user.is_staff %}
     <!--  Open ---------------- -->
     <a class="btn btn-default"
        href="transition/?fromcfid={{poc.commitfest.id}}&tocfid={{workflow.open.id}}">
     {{workflow.open.name}}</a>
    {%endif%}
   {%endif%}

   {%if not cf.isinprogress and workflow.progress %}
    {%if is_committer or request.user.is_staff %}
     <!--  In Progress ---------------- -->
     <a class="btn btn-default"
        href="transition/?fromcfid={{poc.commitfest.id}}&tocfid={{workflow.progress.id}}">
     {{workflow.progress.name}}</a>
    {%endif%}
   {%endif%}
  </div>
  <!--  Resolve ---------------- -->
  <div class="col-md-2 column">
   {%if is_committer or is_author %}
    {%if is_committer %}
     <a class="btn btn-default" href="close/committed/" onclick="return flagCommitted({%if patch.committer%}'{{patch.committer}}'{%elif is_committer%}'{{user.username}}'{%else%}null{%endif%})">Commit</a>
     <a class="btn btn-default" href="close/reject/" onclick="return verify_reject()">Reject</a>
    {%endif%}
    {%if is_author %}
     <a class="btn btn-default" href="close/withdrawn/" onclick="return verify_withdrawn()">Withdraw</a>
    {%endif%}
   {%else%}
    <span>No Actions Available</span>
   {%endif%}
  </div>
 </div>
</div><!-- container -->
