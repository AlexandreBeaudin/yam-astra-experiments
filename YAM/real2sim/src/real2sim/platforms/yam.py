"""YAM model kinematics; no hardware import or connection."""
import numpy as np
class YamKinematics:
    def __init__(self, model_path, joint_names=None, tool_body='link6', body_T_tool=None):
        import mujoco
        self.mj=mujoco;self.model=mujoco.MjModel.from_xml_path(str(model_path));self.data=mujoco.MjData(self.model)
        names=joint_names or ['joint{}'.format(i) for i in range(1,7)]
        self.ids=[mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_JOINT,n) for n in names]
        self.body=mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_BODY,tool_body)
        if min(self.ids)<0 or self.body<0:raise ValueError('Unknown model joint or tool body')
        self.address=self.model.jnt_qposadr[self.ids];self.limits=self.model.jnt_range[self.ids].copy();self.body_T_tool=np.eye(4) if body_T_tool is None else np.asarray(body_T_tool,float)
    def fk(self,q):
        q=np.asarray(q,float)
        if q.shape!=(len(self.ids),) or not np.isfinite(q).all():raise ValueError('Invalid joint vector')
        self.data.qpos[self.address]=q;self.mj.mj_forward(self.model,self.data);T=np.eye(4);T[:3,:3]=self.data.xmat[self.body].reshape(3,3);T[:3,3]=self.data.xpos[self.body]
        return T@self.body_T_tool
    def ik(self,target,seed):
        from scipy.optimize import least_squares
        from scipy.spatial.transform import Rotation
        target=np.asarray(target,float)
        def error(q):
            T=self.fk(q);return np.r_[(T[:3,3]-target[:3,3])*5,Rotation.from_matrix(target[:3,:3].T@T[:3,:3]).as_rotvec()]
        low,high=self.limits.T;fit=least_squares(error,np.clip(seed,low+1e-7,high-1e-7),bounds=(low,high),max_nfev=150);e=error(fit.x)
        return dict(passed=bool(np.linalg.norm(e[:3])/5<.001 and np.linalg.norm(e[3:])<np.deg2rad(1)),q_rad=fit.x.tolist(),position_error_m=float(np.linalg.norm(e[:3])/5),rotation_error_rad=float(np.linalg.norm(e[3:])),collision_checked=False)
