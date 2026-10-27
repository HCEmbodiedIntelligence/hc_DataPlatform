import { BoxGeometry, Group, Mesh, MeshBasicMaterial } from 'three';
import { describe, expect, it, vi } from 'vitest';
import { ParsedRobotCache } from './parsed-robot-cache';

describe('parsed model reuse', () => {
  it('shares geometry while keeping each viewer pose independent', () => {
    const dispose = vi.fn();
    const cache = new ParsedRobotCache<Group>(dispose);
    const template = new Group();
    template.add(new Mesh(new BoxGeometry(), new MeshBasicMaterial()));
    const first = cache.put('actor/scope/content', template);
    first.robot.children[0]!.rotation.x = 1;
    const next = cache.acquire('actor/scope/content')!;
    expect(next.robot.children[0]!.rotation.x).toBeCloseTo(0);
    expect((next.robot.children[0] as Mesh).geometry).toBe((first.robot.children[0] as Mesh).geometry);
    expect(cache.acquire('other-actor/scope/content')).toBeUndefined();
    first.release();
    cache.clear();
    expect(cache.acquire('actor/scope/content')).toBeUndefined();
    expect(dispose).not.toHaveBeenCalled();
    next.release(); next.release();
    expect(dispose).toHaveBeenCalledExactlyOnceWith(template);
  });

  it('does not retain a model above the memory budget', () => {
    const dispose = vi.fn();
    const cache = new ParsedRobotCache<Group>(dispose, 1);
    const template = new Group();
    template.add(new Mesh(new BoxGeometry(), new MeshBasicMaterial()));
    const lease = cache.put('large', template);
    expect(cache.acquire('large')).toBeUndefined();
    expect(dispose).not.toHaveBeenCalled();
    lease.release();
    expect(dispose).toHaveBeenCalledExactlyOnceWith(template);
  });
});
