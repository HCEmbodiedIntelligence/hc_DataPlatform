import { describe, expect, it, vi } from 'vitest';
import {
  Box3,
  BoxGeometry,
  Group,
  Mesh,
  MeshBasicMaterial,
  Quaternion,
  Vector3,
} from 'three';
import {
  applyRobotJointFrame,
  calculateRobotGroundPlacement,
  createRobotGroundPlaneAnchors,
  createRobotAssetUrlResolver,
  groundRobotAtZ,
  stabilizeRobotOnGroundPlane,
} from './threeRobotSceneRuntime';

describe('URDF joint-frame synchronization', () => {
  it('maps source joint radians to URDF joints and ignores invalid samples', () => {
    const setJointValue = vi.fn();

    applyRobotJointFrame(
      { setJointValue },
      { shoulder_source: 0.42, wrist_source: -1.2, invalid: Number.NaN },
      { shoulder_source: 'shoulder_joint', wrist_source: 'wrist_joint' },
    );

    expect(setJointValue).toHaveBeenNthCalledWith(1, 'shoulder_joint', 0.42);
    expect(setJointValue).toHaveBeenNthCalledWith(2, 'wrist_joint', -1.2);
    expect(setJointValue).toHaveBeenCalledTimes(2);
  });
});

describe('URDF dependency URL resolution', () => {
  it('maps URDF-relative mesh and texture requests to their own signed URLs', () => {
    const resolveUrl = createRobotAssetUrlResolver(
      'unitree_g1/g1.urdf',
      {
        'unitree_g1/meshes/head link.STL':
          'https://objects.test/head?signature=head',
        'unitree_g1/textures/body.png':
          'https://objects.test/body?signature=body',
      },
    );

    expect(
      resolveUrl(
        'https://objects.test/private-upload/meshes/head%20link.STL',
      ),
    ).toBe('https://objects.test/head?signature=head');
    expect(resolveUrl('textures/body.png')).toBe(
      'https://objects.test/body?signature=body',
    );
    expect(resolveUrl('https://objects.test/unrelated.stl')).toBe(
      'https://objects.test/unrelated.stl',
    );
  });

  it('prefers an exact full asset path over a shorter relative suffix', () => {
    const resolveUrl = createRobotAssetUrlResolver('models/urdf/robot.urdf', {
      'models/urdf/meshes/shared.stl': 'https://objects.test/relative',
      'models/meshes/shared.stl': 'https://objects.test/full',
    });

    expect(resolveUrl('meshes/shared.stl')).toBe(
      'https://objects.test/relative',
    );
    expect(resolveUrl('models/meshes/shared.stl')).toBe(
      'https://objects.test/full',
    );
  });
});

describe('URDF ground placement', () => {
  it('anchors a sufficiently large ground plane at the initial lowest point', () => {
    const bounds = new Box3(
      new Vector3(-0.5, -0.4, -1),
      new Vector3(0.5, 0.4, 1),
    );

    const placement = calculateRobotGroundPlacement(bounds);

    expect(placement.size).toBeCloseTo(6.4);
    expect(placement.z).toBeCloseTo(-1);
  });

  it('keeps the initial contact plane fixed and moves only the robot root', () => {
    const robot = new Group();
    const ground = new Group();
    const fixedGroundZ = -0.25;
    ground.position.z = fixedGroundZ;
    const body = new Mesh(
      new BoxGeometry(1, 1, 2),
      new MeshBasicMaterial(),
    );
    body.position.z = 2;
    robot.add(body);

    expect(groundRobotAtZ(robot, fixedGroundZ, 0)).toBeCloseTo(-1.25);
    expect(new Box3().setFromObject(robot).min.z).toBeCloseTo(fixedGroundZ);
    expect(ground.position.z).toBe(fixedGroundZ);

    body.position.z = 3;
    expect(groundRobotAtZ(robot, fixedGroundZ, 0)).toBeCloseTo(-2.25);
    expect(robot.position.z).toBeCloseTo(-2.25);
    expect(new Box3().setFromObject(robot).min.z).toBeCloseTo(fixedGroundZ);
    expect(ground.position.z).toBe(fixedGroundZ);

    body.geometry.dispose();
    body.material.dispose();
  });

  it('tracks a foot plane instead of only its lowest point', () => {
    const robot = new Group();
    const foot = new Mesh(
      new BoxGeometry(0.4, 0.8, 0.1),
      new MeshBasicMaterial(),
    );
    foot.name = 'left_foot_link';
    foot.position.z = 0.05;
    robot.add(foot);
    robot.updateMatrixWorld(true);
    const initialBounds = new Box3().setFromObject(robot);
    const anchors = createRobotGroundPlaneAnchors(robot, initialBounds);

    expect(anchors).toHaveLength(1);
    foot.rotation.x = Math.PI / 6;
    const result = stabilizeRobotOnGroundPlane(
      robot,
      anchors,
      0,
      new Vector3(),
      new Quaternion(),
    );
    const stabilizedNormal = anchors[0]!.normalLocal
      .clone()
      .applyQuaternion(foot.getWorldQuaternion(new Quaternion()))
      .normalize();
    const stabilizedPoint = foot.localToWorld(anchors[0]!.pointLocal.clone());

    expect(result?.anchorName).toContain('left_foot_link');
    expect(result?.tiltRadians).toBeCloseTo(Math.PI / 6);
    expect(stabilizedNormal.x).toBeCloseTo(0);
    expect(stabilizedNormal.y).toBeCloseTo(0);
    expect(stabilizedNormal.z).toBeCloseTo(1);
    expect(stabilizedPoint.z).toBeCloseTo(0);

    foot.rotation.x = -Math.PI / 5;
    stabilizeRobotOnGroundPlane(
      robot,
      anchors,
      0,
      new Vector3(),
      new Quaternion(),
    );
    const nextNormal = anchors[0]!.normalLocal
      .clone()
      .applyQuaternion(foot.getWorldQuaternion(new Quaternion()))
      .normalize();
    expect(nextNormal.z).toBeCloseTo(1);

    foot.geometry.dispose();
    foot.material.dispose();
  });
});
